/**
 * Preservation Houston Staff & Field Workbench (Milestone 3).
 *
 * Lazy-loaded via dynamic `import("./staffWorkbench.js?v=20261030e")` strictly when
 * staff or field volunteers open:
 *   - Component A (`#4`): Split-Screen Historian Rapid Audit Mode (`#audit=1`)
 *   - Component B (`#5a`): In-Browser Staff Polygon Editor (`#polyedit=<id>`)
 *   - Component C (`#5b`): Mobile Walk-the-Block Field Survey Mode (`#survey=1`)
 *
 * The top section of this module is strictly DOM-free (zero top-level `window`,
 * `document`, `navigator`, `localStorage`, or `maplibregl` references) so every
 * spatial, archival, multi-footprint, and field-survey helper runs directly in
 * bare Node unit tests (`tests/unit/run_all.mjs`).
 */

import {
  HARRIS_COUNTY_BBOX,
  POLYGON_EDITOR_MAX_VERTICES,
  MAX_GEOMETRY_JSON_CELL_CHARS,
  METERS_PER_DEGREE_LAT,
  SQ_FT_PER_SQ_M,
  FIELD_SURVEY_INTEGRITY_GRADES,
  FIELD_SURVEY_CONDITION_RATINGS,
  FIELD_SURVEY_CONTRIBUTING_VALUES,
  VALID_CONTRIBUTING_VALUES,
  MIN_VALID_YEAR,
  MAX_VALID_YEAR,
  MAX_STANDING_OVERRIDE_YEAR,
  validateOverridePolygonGeometry,
  stripEmbeddedGeometryTag,
  stripEmbeddedFieldSurveyTag,
  getAdminSession,
  submitAdminApprovedOverride,
  submitCorrectionSuggestion,
  getLocalFieldSurveys,
  saveLocalFieldSurvey,
  removeLocalFieldSurvey,
  clearSyncedFieldSurveys,
  exportFieldSurveysToCsv,
  submitFieldSurveyObservation,
  flushPendingFieldSurveys,
  saveSurveyPhotoBlob,
  getSurveyPhotoBlob,
  listSurveyPhotoSummaries,
  deleteSurveyPhotoBlob,
} from "./curatedEdits.js?v=20261030e";
import { normalizeComparableAddress } from "./filterStore.js?v=20261030e";

export const BORDEN_DOWNTOWN_GRID_DEG = -34.65;
export const LS_AUDIT_DECISIONS_KEY = "ph_atlas_audit_decisions_v1";
export const LS_SURVEY_PREFS_KEY = "ph_atlas_field_survey_prefs_v1";
export const AUDIT_UNDESIGNATED_DISTRICT = "Undesignated / Outside HD";

export const AUDIT_CATEGORY_LABELS = Object.freeze({
  CITY_DIRECTORY_EARLIER_THAN_HCAD: "City Directory Earlier Than HCAD",
  CITY_DIRECTORY_ABSENT_THROUGH_1926: "Absent From 1866-1926 Directories",
  LANDMARK_VS_ATLAS_YEAR_MISMATCH: "Landmark vs Atlas Year Conflict",
  ADDRESS_STREET_MISMATCH: "Landmark vs HCAD Street Mismatch",
  SUSPECTED_UNMARKED_LANDMARK_DEMOLITION: "Suspected Unmarked Landmark Loss",
  ARCHITECT_CAREER_WINDOW_CHECK: "Architect Career Window Conflict",
});

export const SURVEY_CONTRIBUTING_VALUES = FIELD_SURVEY_CONTRIBUTING_VALUES;

export const SURVEY_WINDOW_MATERIALS = Object.freeze([
  "Original Wood Sash",
  "Historic Steel Casement",
  "Wood Replacement",
  "Vinyl / Aluminum Replacement",
  "Mixed Original + Replacement",
  "Boarded / Missing",
  "Not Visible",
]);

export const SURVEY_CLADDING_TYPES = Object.freeze([
  "Wood Lap / Tear-Drop Siding",
  "Historic Brick Masonry",
  "Stucco",
  "Wood Shingle",
  "Board & Batten",
  "Fiber-Cement (Hardie)",
  "Vinyl / Aluminum Siding",
  "Stone / CMU",
  "Mixed",
]);

export const SURVEY_PORCH_STATES = Object.freeze([
  "Intact Original Porch",
  "Partial / Altered Columns",
  "Enclosed Historic Porch",
  "Replacement Porch / Deck",
  "No Porch (Original Form)",
  "Removed",
]);

const EARTH_RADIUS_M = 6371008.8;
const DEG_TO_RAD = Math.PI / 180;
const RAD_TO_DEG = 180 / Math.PI;
const FEET_PER_METER = 3.280839895;
const ARC_WORLD_IMAGERY_TPL =
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";
const CONTENTDM_CITYDIR_BASE = "https://cdm17006.contentdm.oclc.org/digital/collection/citydir";

const LEADING_DIRS = new Set([
  "N",
  "S",
  "E",
  "W",
  "NE",
  "NW",
  "SE",
  "SW",
  "NORTH",
  "SOUTH",
  "EAST",
  "WEST",
]);

const STREET_TYPE_SUFFIXES = new Set([
  "ST",
  "STREET",
  "AVE",
  "AV",
  "AVENUE",
  "BLVD",
  "BOULEVARD",
  "RD",
  "ROAD",
  "DR",
  "DRIVE",
  "LN",
  "LANE",
  "CT",
  "COURT",
  "PL",
  "PLACE",
  "PKWY",
  "PARKWAY",
  "FWY",
  "FREEWAY",
  "HWY",
  "HIGHWAY",
  "WAY",
  "CIR",
  "CIRCLE",
  "TRL",
  "TRAIL",
  "TER",
  "TERRACE",
  "SQ",
  "SQUARE",
  "LOOP",
]);

const UNIT_TOKENS = new Set([
  "APT",
  "UNIT",
  "STE",
  "SUITE",
  "BLDG",
  "BUILDING",
  "FL",
  "FLOOR",
  "RM",
  "ROOM",
  "REAR",
]);

function round6(val) {
  return Number(Number(val).toFixed(6));
}

function normalizeHcad13Clean(raw) {
  const digits = String(raw || "").split("#")[0].replace(/\D/g, "").trim();
  return digits.length === 13 ? digits : "";
}

/**
 * Extract an open exterior vertex array (`[[lng, lat], ...]`, without duplicate
 * closing coordinate) from a GeoJSON Feature, Polygon, MultiPolygon (first part),
 * `[ring]`, or coordinate ring.
 */
export function extractEditableRing(geomOrRing) {
  if (!geomOrRing) return [];
  let raw = geomOrRing;
  if (raw && typeof raw === "object" && raw.type === "Feature") {
    raw = raw.geometry;
  }
  if (raw && typeof raw === "object" && !Array.isArray(raw)) {
    if (raw.type === "Polygon" && Array.isArray(raw.coordinates)) {
      raw = raw.coordinates[0] || [];
    } else if (raw.type === "MultiPolygon" && Array.isArray(raw.coordinates)) {
      raw = raw.coordinates[0]?.[0] || [];
    } else {
      return [];
    }
  }
  if (
    Array.isArray(raw) &&
    raw.length >= 1 &&
    Array.isArray(raw[0]) &&
    Array.isArray(raw[0][0]) &&
    typeof raw[0][0][0] === "number"
  ) {
    raw = raw[0];
  }
  if (!Array.isArray(raw)) return [];

  const pts = [];
  for (const pt of raw) {
    if (!Array.isArray(pt) || pt.length < 2) continue;
    const lng = Number(pt[0]);
    const lat = Number(pt[1]);
    if (!Number.isFinite(lng) || !Number.isFinite(lat)) continue;
    pts.push([round6(lng), round6(lat)]);
  }
  while (
    pts.length >= 2 &&
    pts[0][0] === pts[pts.length - 1][0] &&
    pts[0][1] === pts[pts.length - 1][1]
  ) {
    pts.pop();
  }
  return pts;
}

/**
 * Wrap an open or closed ring into a closed GeoJSON Polygon object (without yet
 * discarding invalid topology, so callers can inspect `validateAndNormalizeDraftPolygon`).
 */
export function ringToPolygonGeometry(geomOrRing) {
  const open = extractEditableRing(geomOrRing);
  if (open.length === 0) {
    return { type: "Polygon", coordinates: [[]] };
  }
  const closed = open.map((p) => [round6(p[0]), round6(p[1])]);
  closed.push([closed[0][0], closed[0][1]]);
  return { type: "Polygon", coordinates: [closed] };
}

/**
 * Compute planar area-weighted centroid `[lng, lat]` for an exterior ring or Polygon.
 */
export function computePolygonCentroid(geomOrRing) {
  const ring = extractEditableRing(geomOrRing);
  if (ring.length === 0) return [0, 0];
  if (ring.length < 3) {
    let sx = 0;
    let sy = 0;
    for (const [x, y] of ring) {
      sx += x;
      sy += y;
    }
    return [sx / ring.length, sy / ring.length];
  }
  const refLng = ring[0][0];
  const refLat = ring[0][1];
  let twiceArea = 0;
  let cx = 0;
  let cy = 0;
  for (let i = 0; i < ring.length; i += 1) {
    const x0 = ring[i][0] - refLng;
    const y0 = ring[i][1] - refLat;
    const next = ring[(i + 1) % ring.length];
    const x1 = next[0] - refLng;
    const y1 = next[1] - refLat;
    const cross = x0 * y1 - x1 * y0;
    twiceArea += cross;
    cx += (x0 + x1) * cross;
    cy += (y0 + y1) * cross;
  }
  if (Math.abs(twiceArea) < 1e-14) {
    let sx = 0;
    let sy = 0;
    for (const [x, y] of ring) {
      sx += x;
      sy += y;
    }
    return [sx / ring.length, sy / ring.length];
  }
  const factor = 1 / (3 * twiceArea);
  return [refLng + cx * factor, refLat + cy * factor];
}

/**
 * Compute tangent-plane polygon area in US square feet (`m^2 * SQ_FT_PER_SQ_M`).
 */
export function computePolygonAreaSqFt(geomOrRing) {
  const ring = extractEditableRing(geomOrRing);
  if (ring.length < 3) return 0;
  const [, cLat] = computePolygonCentroid(ring);
  const mPerLat = METERS_PER_DEGREE_LAT;
  const mPerLon = METERS_PER_DEGREE_LAT * Math.cos(cLat * DEG_TO_RAD);
  let sum = 0;
  for (let i = 0; i < ring.length; i += 1) {
    const [lng0, lat0] = ring[i];
    const [lng1, lat1] = ring[(i + 1) % ring.length];
    const x0 = lng0 * mPerLon;
    const y0 = lat0 * mPerLat;
    const x1 = lng1 * mPerLon;
    const y1 = lat1 * mPerLat;
    sum += x0 * y1 - x1 * y0;
  }
  const areaSqM = Math.abs(sum) * 0.5;
  return Number((areaSqM * SQ_FT_PER_SQ_M).toFixed(2));
}

/**
 * Validate and normalize a draft Polygon or coordinate ring using post-rounding
 * 6-decimal topology rules (`3..64` unique vertices, `>= 4.0 m^2`, Harris County
 * bbox, CCW exterior ring, zero proper or collinear self-intersections, cell size cap).
 */
export function validateAndNormalizeDraftPolygon(geomOrRing) {
  let candidate = geomOrRing;
  if (Array.isArray(geomOrRing)) {
    candidate = ringToPolygonGeometry(geomOrRing);
  } else if (geomOrRing && geomOrRing.type === "Feature") {
    candidate = geomOrRing.geometry;
  }
  const res = validateOverridePolygonGeometry(candidate);
  const rawAreaSqM = Number(res.area_sq_m ?? res.areaSqM ?? 0) || 0;
  const rawAreaSqFt = Number.isFinite(Number(res.area_sq_ft))
    ? Number(res.area_sq_ft)
    : Number((rawAreaSqM * SQ_FT_PER_SQ_M).toFixed(1));
  if (!res.ok) {
    const rawRing = extractEditableRing(geomOrRing);
    const errMsg =
      (Array.isArray(res.errors) && res.errors.length > 0 ? res.errors[0] : "") ||
      res.error ||
      "Invalid footprint geometry.";
    return {
      ok: false,
      error: errMsg,
      geometry: null,
      openRing: rawRing,
      areaSqM: rawAreaSqM,
      areaSqFt: rawAreaSqFt,
      vertexCount:
        Number.isFinite(Number(res.vertex_count)) && Number(res.vertex_count) > 0
          ? Number(res.vertex_count)
          : rawRing.length,
      serializedLength: 0,
    };
  }
  const serialized = JSON.stringify(res.geometry);
  const openRing = extractEditableRing(res.geometry);
  const vertexCount =
    Number.isFinite(Number(res.vertex_count ?? res.vertexCount)) &&
    Number(res.vertex_count ?? res.vertexCount) > 0
      ? Number(res.vertex_count ?? res.vertexCount)
      : openRing.length;
  if (serialized.length > MAX_GEOMETRY_JSON_CELL_CHARS) {
    return {
      ok: false,
      error: `Footprint JSON (${serialized.length} chars) exceeds maximum cell budget (${MAX_GEOMETRY_JSON_CELL_CHARS}).`,
      geometry: null,
      openRing,
      areaSqM: rawAreaSqM,
      areaSqFt: rawAreaSqFt,
      vertexCount,
      serializedLength: serialized.length,
    };
  }
  return {
    ok: true,
    error: "",
    geometry: res.geometry,
    openRing,
    areaSqM: rawAreaSqM,
    areaSqFt: rawAreaSqFt,
    vertexCount,
    serializedLength: serialized.length,
  };
}

/**
 * Rotate a Polygon or ring by `angleDeg` counter-clockwise in a local metric
 * tangent plane (`cos(cLat)` at Houston `~29.76N`) around `pivot` (`[lng, lat]`
 * or centroid), avoiding raw lon/lat aspect-ratio skew (`UT-2`).
 *
 * Returns a normalized 6-decimal CCW GeoJSON `Polygon`.
 */
export function rotatePolygonTangentPlane(geomOrRing, angleDeg, pivot = null) {
  const ring = extractEditableRing(geomOrRing);
  if (ring.length < 3) {
    throw new Error("rotatePolygonTangentPlane requires at least 3 vertices.");
  }
  const [pLng, pLat] =
    Array.isArray(pivot) &&
    pivot.length >= 2 &&
    Number.isFinite(Number(pivot[0])) &&
    Number.isFinite(Number(pivot[1]))
      ? [Number(pivot[0]), Number(pivot[1])]
      : computePolygonCentroid(ring);
  const mPerLat = METERS_PER_DEGREE_LAT;
  const mPerLon = METERS_PER_DEGREE_LAT * Math.cos(pLat * DEG_TO_RAD);
  if (!Number.isFinite(mPerLon) || Math.abs(mPerLon) < 1e-6) {
    throw new Error("Invalid pivot latitude for tangent-plane rotation.");
  }
  const rad = Number(angleDeg || 0) * DEG_TO_RAD;
  const cosA = Math.cos(rad);
  const sinA = Math.sin(rad);

  const rotated = ring.map(([lng, lat]) => {
    const dx = (lng - pLng) * mPerLon;
    const dy = (lat - pLat) * mPerLat;
    const rx = dx * cosA - dy * sinA;
    const ry = dx * sinA + dy * cosA;
    return [round6(pLng + rx / mPerLon), round6(pLat + ry / mPerLat)];
  });
  const poly = ringToPolygonGeometry(rotated);
  const checked = validateOverridePolygonGeometry(poly);
  return checked.ok ? checked.geometry : poly;
}

/**
 * Translate a Polygon or ring by `(dxMeters, dyMeters)` East/North in local tangent
 * plane meters around its centroid, returning a normalized 6-decimal GeoJSON `Polygon`.
 */
export function nudgePolygonTangentPlane(geomOrRing, dxMeters = 0, dyMeters = 0) {
  const ring = extractEditableRing(geomOrRing);
  if (ring.length < 3) {
    throw new Error("nudgePolygonTangentPlane requires at least 3 vertices.");
  }
  const [, cLat] = computePolygonCentroid(ring);
  const mPerLat = METERS_PER_DEGREE_LAT;
  const mPerLon = METERS_PER_DEGREE_LAT * Math.cos(cLat * DEG_TO_RAD);
  const dLng = Number(dxMeters || 0) / mPerLon;
  const dLat = Number(dyMeters || 0) / mPerLat;
  const moved = ring.map(([lng, lat]) => [round6(lng + dLng), round6(lat + dLat)]);
  const poly = ringToPolygonGeometry(moved);
  const checked = validateOverridePolygonGeometry(poly);
  return checked.ok ? checked.geometry : poly;
}

/**
 * Create a clean metric rectangular footprint centered on `[lng, lat]`.
 */
export function createRectangleFootprintAroundPoint(
  lng,
  lat,
  { widthM = 12, depthM = 16, angleDeg = 0 } = {}
) {
  const cLng = Number(lng);
  const cLat = Number(lat);
  if (!Number.isFinite(cLng) || !Number.isFinite(cLat)) {
    throw new Error("Center coordinates must be finite numbers.");
  }
  const hw = Math.max(2.2, Number(widthM || 12) / 2);
  const hd = Math.max(2.2, Number(depthM || 16) / 2);
  const mPerLat = METERS_PER_DEGREE_LAT;
  const mPerLon = METERS_PER_DEGREE_LAT * Math.cos(cLat * DEG_TO_RAD);
  const baseRing = [
    [round6(cLng - hw / mPerLon), round6(cLat - hd / mPerLat)],
    [round6(cLng + hw / mPerLon), round6(cLat - hd / mPerLat)],
    [round6(cLng + hw / mPerLon), round6(cLat + hd / mPerLat)],
    [round6(cLng - hw / mPerLon), round6(cLat + hd / mPerLat)],
  ];
  const poly = ringToPolygonGeometry(baseRing);
  if (Number(angleDeg || 0) !== 0) {
    return rotatePolygonTangentPlane(poly, Number(angleDeg), [cLng, cLat]);
  }
  const checked = validateOverridePolygonGeometry(poly);
  return checked.ok ? checked.geometry : poly;
}

/**
 * Update a single vertex of an open ring and evaluate draft topology.
 */
export function updatePolygonVertex(geomOrRing, vertexIndex, lng, lat) {
  const ring = extractEditableRing(geomOrRing);
  const idx = Number(vertexIndex);
  if (!Number.isInteger(idx) || idx < 0 || idx >= ring.length) {
    return {
      ok: false,
      error: "Vertex index out of range.",
      openRing: ring,
      validation: validateAndNormalizeDraftPolygon(ring),
    };
  }
  const nextLng = Number(lng);
  const nextLat = Number(lat);
  if (!Number.isFinite(nextLng) || !Number.isFinite(nextLat)) {
    return {
      ok: false,
      error: "Replacement vertex coordinates must be finite numbers.",
      openRing: ring,
      validation: validateAndNormalizeDraftPolygon(ring),
    };
  }
  const nextRing = ring.map((pt, i) =>
    i === idx ? [round6(nextLng), round6(nextLat)] : [pt[0], pt[1]]
  );
  const validation = validateAndNormalizeDraftPolygon(nextRing);
  return {
    ok: validation.ok,
    error: validation.error,
    openRing: nextRing,
    validation,
  };
}

/**
 * Insert a midpoint vertex on edge `(edgeIndex -> edgeIndex + 1)` up to `64` vertices.
 */
export function insertPolygonMidpointVertex(geomOrRing, edgeIndex) {
  const ring = extractEditableRing(geomOrRing);
  if (ring.length < 2) {
    return {
      ok: false,
      error: "At least 2 vertices are required to split an edge.",
      openRing: ring,
      insertedIndex: -1,
      validation: validateAndNormalizeDraftPolygon(ring),
    };
  }
  if (ring.length >= POLYGON_EDITOR_MAX_VERTICES) {
    return {
      ok: false,
      error: `Footprint already has maximum ${POLYGON_EDITOR_MAX_VERTICES} vertices.`,
      openRing: ring,
      insertedIndex: -1,
      validation: validateAndNormalizeDraftPolygon(ring),
    };
  }
  const idx = Number(edgeIndex);
  if (!Number.isInteger(idx) || idx < 0 || idx >= ring.length) {
    return {
      ok: false,
      error: "Edge index out of range.",
      openRing: ring,
      insertedIndex: -1,
      validation: validateAndNormalizeDraftPolygon(ring),
    };
  }
  const p0 = ring[idx];
  const p1 = ring[(idx + 1) % ring.length];
  const mid = [round6((p0[0] + p1[0]) * 0.5), round6((p0[1] + p1[1]) * 0.5)];
  const nextRing = ring.slice();
  const insertedIndex = idx + 1;
  nextRing.splice(insertedIndex, 0, mid);
  const validation = validateAndNormalizeDraftPolygon(nextRing);
  return {
    ok: true,
    error: validation.error || "",
    openRing: nextRing,
    insertedIndex,
    validation,
  };
}

/**
 * Remove a vertex from an open ring while enforcing the 3-vertex polygon minimum.
 */
export function removePolygonVertex(geomOrRing, vertexIndex) {
  const ring = extractEditableRing(geomOrRing);
  if (ring.length <= 3) {
    return {
      ok: false,
      minVerticesReached: true,
      error: "A building footprint must keep at least 3 vertices.",
      openRing: ring,
      validation: validateAndNormalizeDraftPolygon(ring),
    };
  }
  const idx = Number(vertexIndex);
  if (!Number.isInteger(idx) || idx < 0 || idx >= ring.length) {
    return {
      ok: false,
      minVerticesReached: false,
      error: "Vertex index out of range.",
      openRing: ring,
      validation: validateAndNormalizeDraftPolygon(ring),
    };
  }
  const nextRing = ring.filter((_pt, i) => i !== idx);
  const validation = validateAndNormalizeDraftPolygon(nextRing);
  return {
    ok: validation.ok,
    minVerticesReached: false,
    error: validation.error || "",
    openRing: nextRing,
    validation,
  };
}

/**
 * Build interactive MapLibre / Canvas2D GeoJSON features (`footprint`, `outline`,
 * `midpoint`, `vertex`) for the active polygon editor session.
 */
export function buildPolygonEditorGeoJson(
  geomOrRing,
  { activeVertexIndex = -1, isValid = true, traceClosed = true } = {}
) {
  const ring = extractEditableRing(geomOrRing);
  const features = [];
  if (ring.length >= 3 && traceClosed !== false) {
    const closed = ring.map((p) => [p[0], p[1]]);
    closed.push([ring[0][0], ring[0][1]]);
    features.push({
      type: "Feature",
      properties: {
        feature_kind: "footprint",
        is_valid: isValid ? 1 : 0,
      },
      geometry: {
        type: "Polygon",
        coordinates: [closed],
      },
    });
  }
  if (ring.length >= 2) {
    const lineCoords = ring.map((p) => [p[0], p[1]]);
    if (ring.length >= 3 && traceClosed !== false) {
      lineCoords.push([ring[0][0], ring[0][1]]);
    }
    features.push({
      type: "Feature",
      properties: {
        feature_kind: "outline",
        is_valid: isValid ? 1 : 0,
      },
      geometry: {
        type: "LineString",
        coordinates: lineCoords,
      },
    });
  }
  if (
    ring.length >= 3 &&
    traceClosed !== false &&
    ring.length < POLYGON_EDITOR_MAX_VERTICES
  ) {
    for (let i = 0; i < ring.length; i += 1) {
      const a = ring[i];
      const b = ring[(i + 1) % ring.length];
      features.push({
        type: "Feature",
        properties: {
          feature_kind: "midpoint",
          edge_index: i,
        },
        geometry: {
          type: "Point",
          coordinates: [round6((a[0] + b[0]) * 0.5), round6((a[1] + b[1]) * 0.5)],
        },
      });
    }
  }
  for (let i = 0; i < ring.length; i += 1) {
    features.push({
      type: "Feature",
      properties: {
        feature_kind: "vertex",
        vertex_index: i,
        is_active: i === activeVertexIndex ? 1 : 0,
      },
      geometry: {
        type: "Point",
        coordinates: [ring[i][0], ring[i][1]],
      },
    });
  }
  return {
    type: "FeatureCollection",
    features,
  };
}

/**
 * Evaluate multi-footprint parcel collision risk (`SOP 4 Case D`, `UT-5`).
 * Warns before writing a bare 13-digit override onto a parcel that has multiple
 * distinct footprints or a `MultiPolygon` shard record so accessory garages,
 * bungalows, or campus structures are not hidden.
 */
export function assessMultiFootprintParcelRisk({
  hcadNum = "",
  footprintCount = 0,
  hasMultiPolygon = false,
  subBuildingIds = [],
  existingOverrides = {},
  targetKey = "",
} = {}) {
  const cleanHcad = normalizeHcad13Clean(hcadNum || targetKey);
  const distinctSubIds = new Set();
  for (const sid of Array.isArray(subBuildingIds) ? subBuildingIds : []) {
    const trimmed = String(sid || "").trim();
    if (trimmed) distinctSubIds.add(trimmed);
  }
  let additiveOverrideCount = 0;
  if (cleanHcad && existingOverrides && typeof existingOverrides === "object") {
    for (const [key, ov] of Object.entries(existingOverrides)) {
      if (!ov || ov.suppress_only) continue;
      if (key.startsWith(`${cleanHcad}#`)) {
        distinctSubIds.add(key);
        additiveOverrideCount += 1;
      }
    }
  }
  const effectiveCount = Math.max(
    Number(footprintCount || 0),
    distinctSubIds.size,
    hasMultiPolygon ? 2 : 0
  );
  const isSubTarget = String(targetKey || "").includes("#");
  const hasRisk = Boolean(hasMultiPolygon) || effectiveCount > 1;
  if (!hasRisk) {
    return {
      hcadNum: cleanHcad,
      hasMultiFootprintRisk: false,
      footprintCount: Math.max(1, effectiveCount),
      hasMultiPolygon: false,
      distinctSubIds: Array.from(distinctSubIds),
      additiveOverrideCount,
      warningCode: "",
      recommendedMode: "primary",
      warningMessage: "",
    };
  }
  return {
    hcadNum: cleanHcad,
    hasMultiFootprintRisk: true,
    footprintCount: effectiveCount,
    hasMultiPolygon: Boolean(hasMultiPolygon),
    distinctSubIds: Array.from(distinctSubIds),
    additiveOverrideCount,
    warningCode: "SOP_4_CASE_D",
    recommendedMode: isSubTarget ? "sub_building" : "additive_aux",
    warningMessage:
      `Multi-footprint parcel detected (${effectiveCount} structures${
        hasMultiPolygon ? ", MultiPolygon shard" : ""
      }). Saving on bare HCAD ${cleanHcad} replaces primary shard geometry across the parcel. ` +
      `Use "Add Secondary Structure (${cleanHcad}#aux_N)" to add a garage, ADU, or campus wing ` +
      "while keeping existing buildings visible (SOP 4 Case D).",
  };
}

/**
 * Deterministically allocate the next collision-free `<13digit>#aux_<N>` override key.
 */
export function allocateNextAuxOverrideKey(
  hcadNum,
  existingOverridesMap = {},
  shardSubIds = []
) {
  const cleanHcad = normalizeHcad13Clean(hcadNum);
  if (!cleanHcad) {
    throw new Error("allocateNextAuxOverrideKey requires a valid 13-digit HCAD account.");
  }
  let maxN = 0;
  const seen = new Set();
  const inspectKey = (rawKey) => {
    const k = String(rawKey || "").trim();
    if (!k) return;
    seen.add(k.toLowerCase());
    const prefix = `${cleanHcad}#aux_`;
    if (k.toLowerCase().startsWith(prefix.toLowerCase())) {
      const tail = k.slice(prefix.length);
      if (/^\d+$/.test(tail)) {
        const n = Number.parseInt(tail, 10);
        if (Number.isFinite(n) && n > maxN) maxN = n;
      }
    }
  };
  if (existingOverridesMap && typeof existingOverridesMap === "object") {
    for (const k of Object.keys(existingOverridesMap)) inspectKey(k);
  }
  for (const sid of Array.isArray(shardSubIds) ? shardSubIds : []) {
    inspectKey(sid);
  }
  let candidateN = maxN + 1;
  while (seen.has(`${cleanHcad}#aux_${candidateN}`.toLowerCase())) {
    candidateN += 1;
  }
  return `${cleanHcad}#aux_${candidateN}`;
}

/**
 * Enforce an explicit valid `contributing` classification on every polygon save (`DF-9`)
 * so non-district or non-contributing additions never silently fall back to `"Contributing"`.
 */
export function validatePolygonSaveContributing(contributing) {
  const val = String(contributing || "").trim();
  if (!VALID_CONTRIBUTING_VALUES.has(val)) {
    return {
      ok: false,
      value: "",
      error: `Select an explicit Contributing Status (${Array.from(VALID_CONTRIBUTING_VALUES).join(", ")}).`,
    };
  }
  return { ok: true, value: val, error: "" };
}

// ---------------------------------------------------------------------------
// Historian Audit Queue Pure Helpers (`UT-7`, `IT-4`, `DF-6`, `DF-8`, `DF-11`)
// ---------------------------------------------------------------------------

let _auditStorageOverride = null;
let _auditMemoryFallback = {};

export function _setAuditStorageOverrideForTests(storageOrNull) {
  _auditStorageOverride = storageOrNull || null;
  _auditMemoryFallback = {};
}

function _getAuditStorage() {
  if (_auditStorageOverride) return _auditStorageOverride;
  try {
    if (typeof globalThis !== "undefined" && globalThis.localStorage) {
      return globalThis.localStorage;
    }
  } catch (_err) {
    return null;
  }
  return null;
}

export function loadAuditDecisions() {
  const merged = { ..._auditMemoryFallback };
  const store = _getAuditStorage();
  if (!store) return merged;
  try {
    const raw = store.getItem(LS_AUDIT_DECISIONS_KEY);
    if (!raw) return merged;
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return { ...parsed, ...merged };
    }
  } catch (_err) {
    return merged;
  }
  return merged;
}

export function saveAuditDecision(findingId, status, note = "") {
  const fid = String(findingId || "").trim();
  if (!fid) return loadAuditDecisions();
  const cleanStatus = String(status || "pending").trim().toLowerCase();
  const current = loadAuditDecisions();
  const entry = {
    status: cleanStatus,
    note: String(note || "").trim(),
    updated_at: new Date().toISOString(),
  };
  current[fid] = entry;
  _auditMemoryFallback[fid] = entry;
  const store = _getAuditStorage();
  if (store) {
    try {
      store.setItem(LS_AUDIT_DECISIONS_KEY, JSON.stringify(current));
    } catch (_err) {
      // Safari Private Mode / QuotaExceededError safe fallback (`DF-11`)
    }
  }
  return current;
}

/**
 * Strip house number, unit suffixes, cardinal directionals, and street type tokens
 * so `citydir_index.json` `historic_streets` lookups resolve reliably (`UT-7`).
 * Example: `"1412 1/2 N Emancipation Ave Apt B"` -> `"EMANCIPATION"`.
 */
export function normalizeStreetStemForAliases(address) {
  const raw = String(address || "")
    .toUpperCase()
    .replace(/[.,#()]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (!raw) return "";
  let tokens = raw.split(" ").filter(Boolean);

  // Strip leading house numbers, hyphenated ranges, and fractions (`1412`, `1412-B`, `1/2`)
  while (
    tokens.length > 0 &&
    (/^\d+[A-Z]?$/.test(tokens[0]) ||
      /^\d+-\d+[A-Z]?$/.test(tokens[0]) ||
      /^\d+\/\d+$/.test(tokens[0]))
  ) {
    tokens.shift();
  }

  // Cut trailing apartment/suite/unit descriptors (`APT 2`, `STE 400`, `REAR`)
  for (let i = 1; i < tokens.length; i += 1) {
    if (UNIT_TOKENS.has(tokens[i])) {
      tokens = tokens.slice(0, i);
      break;
    }
  }

  // Strip leading cardinal directional (`N SAN JACINTO ST` -> `SAN JACINTO ST`)
  if (tokens.length > 1 && LEADING_DIRS.has(tokens[0])) {
    tokens.shift();
  }
  // Strip trailing cardinal directional (`WESTHEIMER RD W` -> `WESTHEIMER RD`)
  if (tokens.length > 1 && LEADING_DIRS.has(tokens[tokens.length - 1])) {
    tokens.pop();
  }
  // Strip trailing street suffix (`SAN JACINTO ST` -> `SAN JACINTO`)
  while (tokens.length > 1 && STREET_TYPE_SUFFIXES.has(tokens[tokens.length - 1])) {
    tokens.pop();
  }
  // Strip secondary trailing directional if written before suffix
  if (tokens.length > 1 && LEADING_DIRS.has(tokens[tokens.length - 1])) {
    tokens.pop();
  }
  return tokens.join(" ").trim();
}

/**
 * Expand compact `historical_audit_queue.json` records into hydrated workstation items.
 */
export function hydrateAuditQueuePayload(queueDoc) {
  if (!queueDoc || typeof queueDoc !== "object" || !Array.isArray(queueDoc.items)) {
    return [];
  }
  const defaults = queueDoc.defaults || {};
  const defaultDistrict = defaults.district || AUDIT_UNDESIGNATED_DISTRICT;
  const defaultContrib = defaults.contributing || "Outside Historic District";
  const absentEv =
    defaults.absent_1926_evidence ||
    "Address absent across indexed 1866-1926 Houston City Directories despite pre-1926 HCAD year.";
  const cdmPrefix =
    defaults.cdm_search_prefix || `${CONTENTDM_CITYDIR_BASE}/search/searchterm/`;
  const actionsMap = defaults.actions || {};
  const sourcesMap = defaults.sources || {};

  return queueDoc.items.map((it, idx) => {
    const cat = String(it.cat || "").trim();
    const address = String(it.a || "").trim();
    const hcad = normalizeHcad13Clean(it.h) || String(it.h || "").trim();
    const lng = Array.isArray(it.ll) ? Number(it.ll[0]) : Number(it.lng);
    const lat = Array.isArray(it.ll) ? Number(it.ll[1]) : Number(it.lat);
    const dp = Number.isInteger(it.dp) && it.dp > 0 ? it.dp : null;
    let sourceUrl = String(it.u || "").trim();
    if (!sourceUrl) {
      if (dp) {
        sourceUrl = `${CONTENTDM_CITYDIR_BASE}/id/${dp}/rec/1`;
      } else if (address) {
        sourceUrl = `${cdmPrefix}${encodeURIComponent(address)}`;
      } else {
        sourceUrl = CONTENTDM_CITYDIR_BASE;
      }
    }
    const evidence =
      String(it.ev || "").trim() ||
      (cat === "CITY_DIRECTORY_ABSENT_THROUGH_1926" ? absentEv : "");
    const district = String(it.d || "").trim() || defaultDistrict;
    return {
      index: idx,
      fid: String(it.fid || `${cat}:${hcad || idx}`),
      finding_id: String(it.fid || `${cat}:${hcad || idx}`),
      priority: Number(it.p || 50),
      category: cat,
      category_label: AUDIT_CATEGORY_LABELS[cat] || cat,
      hcad_num: hcad,
      building_id: String(it.bid || "").trim(),
      building_name: String(it.n || "").trim(),
      address,
      lng: Number.isFinite(lng) ? lng : -95.3635,
      lat: Number.isFinite(lat) ? lat : 29.7595,
      atlas_year: Number.isFinite(Number(it.ay)) ? Number(it.ay) : null,
      hcad_year: Number.isFinite(Number(it.hy)) ? Number(it.hy) : null,
      suggested_year: Number.isFinite(Number(it.sy)) ? Number(it.sy) : null,
      displayed_claim: String(it.cl || "").trim(),
      conflicting_evidence: evidence,
      source_type: String(it.st || sourcesMap[cat] || "Archival Cross-Check").trim(),
      source_url: sourceUrl,
      citydir_pointer: dp,
      district,
      contributing: String(it.cnt || defaultContrib).trim(),
      recommended_action: String(it.act || actionsMap[cat] || "").trim(),
      landmark_code: String(it.lmc || "").trim(),
      architect: String(it.arc || "").trim(),
    };
  });
}

/**
 * Category-scoped curated-override resolution check (`IT-4`, `DF-8`).
 * Ensures accepting a year fix on a shared HCAD (e.g. `0200670000010`) does NOT
 * falsely resolve an independent address mismatch or architect-window finding on
 * that same parcel unless that specific attribute changed.
 */
export function isFindingResolvedByOverrides(item, overridesMap = {}) {
  if (!item || !overridesMap || typeof overridesMap !== "object") return false;
  const bid = String(item.building_id || "").trim();
  const hcad = String(item.hcad_num || "").trim();
  const ov = (bid && overridesMap[bid]) || (hcad && overridesMap[hcad]) || null;
  if (!ov || ov.suppress_only) return false;

  const isDemo =
    Boolean(ov.is_demolished_vacant_lot) ||
    Number(ov.demolished_year) >= MIN_VALID_YEAR ||
    /demolish|vacant|replaced/i.test(String(ov.issue_reported || ""));

  const ovYear = Number(ov.year_built);
  const hasValidOvYear = Number.isFinite(ovYear) && ovYear >= MIN_VALID_YEAR;
  const yearChanged =
    hasValidOvYear &&
    (item.atlas_year == null || ovYear !== Number(item.atlas_year));

  switch (item.category) {
    case "CITY_DIRECTORY_EARLIER_THAN_HCAD":
    case "CITY_DIRECTORY_ABSENT_THROUGH_1926":
    case "LANDMARK_VS_ATLAS_YEAR_MISMATCH":
      return isDemo || yearChanged;
    case "ADDRESS_STREET_MISMATCH": {
      const ovAddr = normalizeComparableAddress(ov.address || "");
      const itAddr = normalizeComparableAddress(item.address || "");
      return Boolean(ovAddr && ovAddr !== itAddr);
    }
    case "ARCHITECT_CAREER_WINDOW_CHECK": {
      const ovArch = String(ov.architect || "").trim().toLowerCase();
      const itArch = String(item.architect || "").trim().toLowerCase();
      return Boolean((ovArch && ovArch !== itArch) || yearChanged);
    }
    case "SUSPECTED_UNMARKED_LANDMARK_DEMOLITION":
      return isDemo;
    default:
      return isDemo || yearChanged;
  }
}

export function resolveAuditItemStatus(item, decisionsMap = {}, overridesMap = {}) {
  if (!item) return "pending";
  const fid = String(item.fid || item.finding_id || "");
  const dec = fid && decisionsMap ? decisionsMap[fid] : null;
  const decStatus = typeof dec === "string" ? dec : dec?.status;
  if (decStatus === "accepted" || decStatus === "dismissed" || decStatus === "skipped") {
    return decStatus;
  }
  if (isFindingResolvedByOverrides(item, overridesMap)) {
    return "resolved_override";
  }
  return "pending";
}

/**
 * Filter hydrated audit items by category, district (including explicit
 * `"Undesignated / Outside HD"` and `"designated_only"`, `DF-8`), status, and text query.
 */
export function filterAuditQueueItems(
  items,
  filters = {},
  decisionsMap = {},
  overridesMap = {}
) {
  if (!Array.isArray(items)) return [];
  const catFilter = String(filters.category || "all").trim();
  const distFilter = String(filters.district || "all").trim();
  const statusFilter = String(filters.status || "all").trim().toLowerCase();
  const q = String(filters.query || "").trim().toLowerCase();

  return items.filter((item) => {
    if (catFilter && catFilter !== "all" && item.category !== catFilter) {
      return false;
    }
    const itemDist = String(item.district || AUDIT_UNDESIGNATED_DISTRICT).trim();
    if (distFilter && distFilter !== "all") {
      if (distFilter === "designated_only") {
        if (!itemDist || itemDist === AUDIT_UNDESIGNATED_DISTRICT) return false;
      } else if (itemDist !== distFilter) {
        return false;
      }
    }
    const status = resolveAuditItemStatus(item, decisionsMap, overridesMap);
    if (statusFilter && statusFilter !== "all") {
      if (statusFilter === "actionable" || statusFilter === "pending") {
        if (status !== "pending") return false;
      } else if (statusFilter === "resolved") {
        if (status !== "accepted" && status !== "resolved_override") return false;
      } else if (status !== statusFilter) {
        return false;
      }
    }
    if (q) {
      const hay = [
        item.address,
        item.hcad_num,
        item.building_name,
        item.architect,
        item.district,
        item.displayed_claim,
        item.conflicting_evidence,
        item.fid,
        item.category_label,
      ]
        .join(" ")
        .toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

export function computeAuditProgressStats(items, decisionsMap = {}, overridesMap = {}) {
  const list = Array.isArray(items) ? items : [];
  let pending = 0;
  let accepted = 0;
  let resolvedOverride = 0;
  let dismissed = 0;
  let skipped = 0;
  for (const item of list) {
    const st = resolveAuditItemStatus(item, decisionsMap, overridesMap);
    if (st === "accepted") accepted += 1;
    else if (st === "resolved_override") resolvedOverride += 1;
    else if (st === "dismissed") dismissed += 1;
    else if (st === "skipped") skipped += 1;
    else pending += 1;
  }
  const total = list.length;
  const completed = accepted + resolvedOverride + dismissed;
  const percentComplete = total > 0 ? Number(((completed / total) * 100).toFixed(1)) : 0;
  return {
    total,
    pending,
    accepted,
    resolvedOverride,
    dismissed,
    skipped,
    completed,
    percentComplete,
  };
}

function pointInRing2D(lng, lat, ring) {
  if (!Array.isArray(ring) || ring.length < 3) return false;
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
    const xi = Number(ring[i][0]);
    const yi = Number(ring[i][1]);
    const xj = Number(ring[j][0]);
    const yj = Number(ring[j][1]);
    const intersect =
      yi > lat !== yj > lat &&
      lng < ((xj - xi) * (lat - yi)) / (yj - yi || 1e-15) + xi;
    if (intersect) inside = !inside;
  }
  return inside;
}

export function pointInGeoJsonGeometry(lng, lat, geometry) {
  if (!geometry || !Number.isFinite(Number(lng)) || !Number.isFinite(Number(lat))) {
    return false;
  }
  const x = Number(lng);
  const y = Number(lat);
  const polys =
    geometry.type === "Polygon"
      ? [geometry.coordinates]
      : geometry.type === "MultiPolygon"
      ? geometry.coordinates
      : [];
  for (const poly of polys) {
    if (!Array.isArray(poly) || !poly[0]) continue;
    if (!pointInRing2D(x, y, poly[0])) continue;
    let inHole = false;
    for (let h = 1; h < poly.length; h += 1) {
      if (pointInRing2D(x, y, poly[h])) {
        inHole = true;
        break;
      }
    }
    if (!inHole) return true;
  }
  return false;
}

/**
 * Resolve deep archival verification metadata for a single audit item:
 *   - 1866-1926 City Directory OCR hit (`by_hcad`), ContentDM page pointer (`p`),
 *     absent-through-1926 signal, historic street rename aliases (`historic_streets`),
 *     and primary milestone volume chips.
 *   - 1877-1955 Sanborn Fire Insurance spatial coverage (`volume_boundaries` bare Array)
 *     plus local UT PCL WebP thumbnail previews (no cross-origin `.jpg` `<img>` loads).
 */
export function resolveAuditItemArchivalContext(
  item,
  { citydirIndex = null, sanbornCatalog = null } = {}
) {
  const hcad = normalizeHcad13Clean(item?.hcad_num);
  const address = String(item?.address || "").trim();
  const houseMatch = address.match(/^\s*(\d+[A-Z]?)\b/i);
  const houseNum = houseMatch ? houseMatch[1] : "";
  const streetStem = normalizeStreetStemForAliases(address);

  const citydirHit =
    hcad && citydirIndex?.by_hcad ? citydirIndex.by_hcad[hcad] || null : null;
  const absentList = Array.isArray(citydirIndex?.absent_through_1926)
    ? citydirIndex.absent_through_1926
    : [];
  const isAbsentThrough1926 =
    item?.category === "CITY_DIRECTORY_ABSENT_THROUGH_1926" ||
    Boolean(hcad && absentList.includes(hcad));

  const pointerInt =
    Number.isInteger(item?.citydir_pointer) && item.citydir_pointer > 0
      ? item.citydir_pointer
      : Number.isInteger(citydirHit?.p) && citydirHit.p > 0
      ? citydirHit.p
      : null;
  const directPageUrl = pointerInt
    ? `${CONTENTDM_CITYDIR_BASE}/id/${pointerInt}/rec/1`
    : "";
  const addressSearchUrl = address
    ? `${CONTENTDM_CITYDIR_BASE}/search/searchterm/${encodeURIComponent(address)}`
    : CONTENTDM_CITYDIR_BASE;

  const rawAliases =
    streetStem && citydirIndex?.historic_streets
      ? citydirIndex.historic_streets[streetStem] || []
      : [];
  const streetAliases = rawAliases.map((entry) => {
    const former = String(entry.former || "").trim();
    const historicQuery = houseNum ? `${houseNum} ${former}` : former;
    return {
      modernStem: streetStem,
      former,
      era: String(entry.era || "").trim(),
      note: String(entry.note || "").trim(),
      searchQuery: historicQuery,
      searchUrl: `${CONTENTDM_CITYDIR_BASE}/search/searchterm/${encodeURIComponent(historicQuery)}`,
    };
  });

  const milestoneVolumes = Array.isArray(citydirIndex?.volumes)
    ? citydirIndex.volumes
        .filter((v) => v && v.primary !== false && Number.isInteger(v.pointer) && v.pointer > 0)
        .map((v) => ({
          year: v.year,
          label: String(v.label || v.year),
          title: String(v.title || ""),
          url: `${CONTENTDM_CITYDIR_BASE}/id/${v.pointer}`,
        }))
    : [];

  // Spatial Sanborn matching over bare Array `sanbornCatalog.volume_boundaries`
  const boundaries = Array.isArray(sanbornCatalog?.volume_boundaries)
    ? sanbornCatalog.volume_boundaries
    : Array.isArray(sanbornCatalog?.volume_boundaries?.features)
    ? sanbornCatalog.volume_boundaries.features
    : [];
  const matchedBoundaries = [];
  for (const feat of boundaries) {
    if (!feat?.geometry) continue;
    if (pointInGeoJsonGeometry(item?.lng, item?.lat, feat.geometry)) {
      const p = feat.properties || {};
      matchedBoundaries.push({
        title: String(p.Title || p.service_name || "Sanborn Volume").trim(),
        volYear: String(p.VolYear || p.sanborn_era || "").trim(),
        era: String(p.sanborn_era || "").trim(),
        imageUrl: String(p.IMAGE_URL || "").trim(),
        notes: String(p.Notes || "").trim(),
      });
    }
  }
  matchedBoundaries.sort((a, b) =>
    String(a.era || a.volYear).localeCompare(String(b.era || b.volYear))
  );

  const matchedEras = new Set(matchedBoundaries.map((b) => b.era).filter(Boolean));
  const allSanbornVolumes = Array.isArray(sanbornCatalog?.volumes)
    ? sanbornCatalog.volumes
    : [];
  const sanbornVolumes = allSanbornVolumes
    .filter((v) => matchedEras.size === 0 || matchedEras.has(String(v.era || "")))
    .slice(0, 8)
    .map((vol) => {
      const sheets = Array.isArray(vol.sheets) ? vol.sheets : [];
      const featured = sheets.filter((s) => s && s.is_featured).slice(0, 3);
      const sampleSheets = (featured.length > 0 ? featured : sheets.slice(0, 2)).map((s) => {
        const rawThumb = String(s.thumb_url || s.preview_url || "").trim();
        // Enforce local relative WebP path only for `<img>` tags (no cross-origin UT PCL `.jpg`)
        const safeLocalThumb = /^https?:\/\//i.test(rawThumb)
          ? ""
          : rawThumb.startsWith("public/") || rawThumb.startsWith("/")
          ? rawThumb
          : rawThumb
          ? `public/${rawThumb.replace(/^\.?\//, "")}`
          : "";
        return {
          code: String(s.code || ""),
          label: String(s.label || s.code || "Sheet"),
          localThumbUrl: safeLocalThumb,
          externalUrl: String(s.loc_url || s.hires_url || vol.loc_url || "").trim(),
        };
      });
      return {
        id: String(vol.id || ""),
        era: String(vol.era || ""),
        date: String(vol.date || ""),
        title: String(vol.title || ""),
        source: String(vol.source || ""),
        sheetCount: Number(vol.sheet_count || sheets.length || 0),
        locUrl: String(vol.loc_url || vol.loc_resource_url || "").trim(),
        sampleSheets,
      };
    });

  return {
    streetStem,
    houseNumber: houseNum,
    citydir: {
      hit: citydirHit,
      earliestYear: citydirHit?.e ?? null,
      presentYears: Array.isArray(citydirHit?.y) ? citydirHit.y : [],
      ocrExcerpt: String(citydirHit?.c || "").trim(),
      matchedField: String(citydirHit?.f || "").trim(),
      pointer: pointerInt,
      directPageUrl,
      addressSearchUrl,
      isAbsentThrough1926,
      streetAliases,
      milestoneVolumes,
    },
    sanborn: {
      matchedBoundaries,
      volumes: sanbornVolumes,
    },
  };
}

/**
 * Build a zoomed satellite roof preview descriptor pinned strictly to native
 * ArcGIS World Imagery `maxTileZoom = 18` (`DF-6`), CSS-scaled up for `displayZoom`
 * (`19.5..20.5`) so tile requests NEVER 404 or return blank imagery at `z=19..20`.
 */
export function buildSatellitePreviewTileSpec(
  lng,
  lat,
  { displayZoom = 19.5, nativeMaxZoom = 18, viewportPx = 220 } = {}
) {
  const cLng = Number.isFinite(Number(lng)) ? Number(lng) : -95.3635;
  const cLat = Math.max(
    -85.05,
    Math.min(85.05, Number.isFinite(Number(lat)) ? Number(lat) : 29.7595)
  );
  const clampedNativeMax = Math.max(10, Math.min(18, Math.floor(Number(nativeMaxZoom) || 18)));
  const targetDisplayZoom = Math.max(14, Math.min(20.5, Number(displayZoom) || 19.5));
  const tileZ = Math.min(clampedNativeMax, Math.floor(targetDisplayZoom));
  const n = 2 ** tileZ;
  const fx = ((cLng + 180) / 360) * n;
  const sinLat = Math.sin(cLat * DEG_TO_RAD);
  const fy = (0.5 - Math.log((1 + sinLat) / (1 - sinLat)) / (4 * Math.PI)) * n;
  const centerTileX = Math.floor(fx);
  const centerTileY = Math.floor(fy);
  const offsetWithinTileX = (fx - centerTileX) * 256;
  const offsetWithinTileY = (fy - centerTileY) * 256;
  const scale = Number((2 ** (targetDisplayZoom - tileZ)).toFixed(4));

  const tiles = [];
  for (let dy = -1; dy <= 1; dy += 1) {
    for (let dx = -1; dx <= 1; dx += 1) {
      const tx = centerTileX + dx;
      const ty = centerTileY + dy;
      if (tx < 0 || ty < 0 || tx >= n || ty >= n) continue;
      tiles.push({
        dx,
        dy,
        z: tileZ,
        x: tx,
        y: ty,
        gridCol: dx + 1,
        gridRow: dy + 1,
        url: ARC_WORLD_IMAGERY_TPL.replace("{z}", String(tileZ))
          .replace("{y}", String(ty))
          .replace("{x}", String(tx)),
      });
    }
  }
  return {
    lng: cLng,
    lat: cLat,
    tileZ,
    displayZoom: targetDisplayZoom,
    scale,
    viewportPx: Math.max(120, Number(viewportPx) || 220),
    centerTileX,
    centerTileY,
    offsetWithinTileX: Number(offsetWithinTileX.toFixed(2)),
    offsetWithinTileY: Number(offsetWithinTileY.toFixed(2)),
    tiles,
  };
}

// ---------------------------------------------------------------------------
// Walk-the-Block Field Survey Pure Helpers (`UT-8`, `DF-4`, `DF-11`)
// ---------------------------------------------------------------------------

export function haversineDistanceMeters(lng1, lat1, lng2, lat2) {
  const lonA = Number(lng1) * DEG_TO_RAD;
  const latA = Number(lat1) * DEG_TO_RAD;
  const lonB = Number(lng2) * DEG_TO_RAD;
  const latB = Number(lat2) * DEG_TO_RAD;
  if (
    !Number.isFinite(lonA) ||
    !Number.isFinite(latA) ||
    !Number.isFinite(lonB) ||
    !Number.isFinite(latB)
  ) {
    return Infinity;
  }
  const dLat = latB - latA;
  const dLon = lonB - lonA;
  const sinDLat = Math.sin(dLat * 0.5);
  const sinDLon = Math.sin(dLon * 0.5);
  const a =
    sinDLat * sinDLat + Math.cos(latA) * Math.cos(latB) * sinDLon * sinDLon;
  return 2 * EARTH_RADIUS_M * Math.atan2(Math.sqrt(a), Math.sqrt(Math.max(0, 1 - a)));
}

export function compassBearingDegrees(lng1, lat1, lng2, lat2) {
  const lonA = Number(lng1) * DEG_TO_RAD;
  const latA = Number(lat1) * DEG_TO_RAD;
  const lonB = Number(lng2) * DEG_TO_RAD;
  const latB = Number(lat2) * DEG_TO_RAD;
  const dLon = lonB - lonA;
  const y = Math.sin(dLon) * Math.cos(latB);
  const x =
    Math.cos(latA) * Math.sin(latB) -
    Math.sin(latA) * Math.cos(latB) * Math.cos(dLon);
  return ((Math.atan2(y, x) * RAD_TO_DEG) % 360 + 360) % 360;
}

export function bearingToCardinal8(bearingDeg) {
  const dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
  const norm = ((Number(bearingDeg || 0) % 360) + 360) % 360;
  return dirs[Math.round(norm / 45) % 8];
}

export function angularDifferenceDegrees(degA, degB) {
  const diff = Math.abs(Number(degA || 0) - Number(degB || 0)) % 360;
  return diff > 180 ? 360 - diff : diff;
}

export function evaluateGpsAccuracy(accuracyMeters) {
  const isMissingInput =
    accuracyMeters === null ||
    accuracyMeters === undefined ||
    typeof accuracyMeters === "boolean" ||
    (typeof accuracyMeters === "string" && !accuracyMeters.trim());
  const acc = isMissingInput ? NaN : Number(accuracyMeters);
  if (!Number.isFinite(acc) || acc <= 0) {
    return {
      accuracyMeters: null,
      accuracyFeet: null,
      isLowAccuracy: false,
      allowAutoSnap: false,
      badgeTone: "neutral",
      label: "Manual map aim",
      warningMessage: "",
    };
  }
  const roundedM = Number(acc.toFixed(1));
  const roundedFt = Math.round(acc * FEET_PER_METER);
  const isLowAccuracy = acc > 25;
  return {
    accuracyMeters: roundedM,
    accuracyFeet: roundedFt,
    isLowAccuracy,
    allowAutoSnap: !isLowAccuracy,
    badgeTone: isLowAccuracy ? "warn" : acc <= 10 ? "good" : "ok",
    label: `GPS +/-${Math.round(acc)}m (${roundedFt}ft)`,
    warningMessage: isLowAccuracy
      ? `Low GPS accuracy (+/-${Math.round(acc)}m / ${roundedFt}ft). Verify the house number on the mailbox or tap the exact roof on the map.`
      : "",
  };
}

function extractFeaturePointLngLat(feature) {
  if (!feature || typeof feature !== "object") return null;
  const p = feature.properties || {};
  const g = feature.geometry;
  if (g?.type === "Point" && Array.isArray(g.coordinates) && g.coordinates.length >= 2) {
    const [lng, lat] = g.coordinates;
    if (Number.isFinite(Number(lng)) && Number.isFinite(Number(lat))) {
      return [Number(lng), Number(lat)];
    }
  }
  if (g?.type === "Polygon" || g?.type === "MultiPolygon") {
    const ring = extractEditableRing(g);
    if (ring.length > 0) {
      const [lng, lat] = computePolygonCentroid(ring);
      if (Number.isFinite(lng) && Number.isFinite(lat)) return [lng, lat];
    }
  }
  const pLng = Number(p.lng ?? p.lon ?? p.longitude);
  const pLat = Number(p.lat ?? p.latitude);
  if (Number.isFinite(pLng) && Number.isFinite(pLat)) return [pLng, pLat];
  return null;
}

function splitAddressHouseAndStreet(address) {
  const trimmed = String(address || "").trim();
  if (!trimmed) return { houseNumber: "", streetRest: "", sideParity: "" };
  const m = trimmed.match(/^\s*(\d+[A-Z]?)\s+(.*)$/i);
  if (!m) return { houseNumber: "", streetRest: trimmed, sideParity: "" };
  const digits = parseInt(m[1].replace(/\D/g, ""), 10);
  const sideParity = Number.isFinite(digits) ? (digits % 2 === 0 ? "even" : "odd") : "";
  return {
    houseNumber: m[1].toUpperCase(),
    streetRest: m[2].trim(),
    sideParity,
  };
}

/**
 * Rank and deduplicate nearby building/parcel footprints monotonically by distance
 * from `(originLng, originLat)` (`UT-8`).
 */
export function rankNearbySurveyCandidates(
  features,
  originLng,
  originLat,
  { surveyedKeys = new Set(), maxResults = 20, maxRadiusMeters = 140 } = {}
) {
  if (
    !Array.isArray(features) ||
    !Number.isFinite(Number(originLng)) ||
    !Number.isFinite(Number(originLat))
  ) {
    return [];
  }
  const surveyed =
    surveyedKeys instanceof Set ? surveyedKeys : new Set(surveyedKeys || []);
  const byKey = new Map();

  for (const feat of features) {
    const pt = extractFeaturePointLngLat(feat);
    if (!pt) continue;
    const props = feat.properties || {};
    if (props.is_demolished_vacant_lot && props.suppress_only) continue;

    const hcad = normalizeHcad13Clean(props.hcad_num || props.HCAD_NUM);
    const bid = String(props.building_id || props.id || feat.id || "").trim();
    const address = String(props.address || props.site_addr || "").trim();
    const dedupeKey = bid || hcad || normalizeComparableAddress(address);
    if (!dedupeKey) continue;

    const distM = haversineDistanceMeters(originLng, originLat, pt[0], pt[1]);
    if (!Number.isFinite(distM) || distM > maxRadiusMeters) continue;

    const bearing = compassBearingDegrees(originLng, originLat, pt[0], pt[1]);
    const { houseNumber, streetRest, sideParity } = splitAddressHouseAndStreet(address);
    const yearNum = Number(props.year_built);
    const candidate = {
      key: dedupeKey,
      building_id: bid,
      hcad_num: hcad,
      address,
      houseNumber,
      streetDisplay: streetRest || address || (hcad ? `HCAD ${hcad}` : dedupeKey),
      streetStem: normalizeStreetStemForAliases(address),
      sideParity,
      building_name: String(props.building_name || props.name || "").trim(),
      district: String(props.historic_district || props.district || "").trim(),
      contributing: String(props.contributing || "").trim(),
      year_built: Number.isFinite(yearNum) && yearNum >= MIN_VALID_YEAR ? yearNum : null,
      lng: round6(pt[0]),
      lat: round6(pt[1]),
      distanceMeters: Number(distM.toFixed(2)),
      distanceFeet: Math.round(distM * FEET_PER_METER),
      bearingDeg: Number(bearing.toFixed(1)),
      cardinal: bearingToCardinal8(bearing),
      isSurveyed:
        surveyed.has(dedupeKey) ||
        Boolean(bid && surveyed.has(bid)) ||
        Boolean(hcad && surveyed.has(hcad)),
      properties: { ...props, lng: round6(pt[0]), lat: round6(pt[1]) },
    };

    const prev = byKey.get(dedupeKey);
    if (!prev) {
      byKey.set(dedupeKey, candidate);
    } else {
      const prevScore = (prev.address ? 2 : 0) + (prev.year_built ? 1 : 0);
      const nextScore = (candidate.address ? 2 : 0) + (candidate.year_built ? 1 : 0);
      if (
        nextScore > prevScore ||
        (nextScore === prevScore && candidate.distanceMeters < prev.distanceMeters)
      ) {
        byKey.set(dedupeKey, candidate);
      }
    }
  }

  const ranked = Array.from(byKey.values());
  ranked.sort((a, b) => {
    if (a.distanceMeters !== b.distanceMeters) {
      return a.distanceMeters - b.distanceMeters;
    }
    return a.key.localeCompare(b.key);
  });
  return ranked.slice(0, Math.max(1, Number(maxResults) || 20));
}

/**
 * Pick the next unsurveyed house when the surveyor taps `Save & Next House`.
 * Prefers the closest unsurveyed structure forward along `walkingBearingDeg`
 * on the same street and blockface parity, gracefully falling back to the
 * nearest unsurveyed neighbor.
 */
export function pickNextBlockfaceCandidate(
  candidates,
  currentCandidateOrKey,
  { walkingBearingDeg = null, surveyedKeys = new Set() } = {}
) {
  if (!Array.isArray(candidates) || candidates.length === 0) return null;
  const surveyed =
    surveyedKeys instanceof Set ? surveyedKeys : new Set(surveyedKeys || []);
  const curKey =
    typeof currentCandidateOrKey === "string"
      ? currentCandidateOrKey
      : String(currentCandidateOrKey?.key || "");
  const curObj =
    (typeof currentCandidateOrKey === "object" && currentCandidateOrKey) ||
    candidates.find((c) => c.key === curKey) ||
    null;

  const unsurveyed = candidates.filter(
    (c) =>
      c &&
      c.key !== curKey &&
      !c.isSurveyed &&
      !surveyed.has(c.key) &&
      !(c.hcad_num && surveyed.has(c.hcad_num))
  );
  if (unsurveyed.length === 0) return null;
  if (!curObj) return unsurveyed[0];

  const hasBearing = Number.isFinite(Number(walkingBearingDeg));
  const scored = unsurveyed.map((c) => {
    const stepDist = haversineDistanceMeters(curObj.lng, curObj.lat, c.lng, c.lat);
    const stepBearing = compassBearingDegrees(curObj.lng, curObj.lat, c.lng, c.lat);
    let penalty = 0;
    if (curObj.streetStem && c.streetStem && c.streetStem !== curObj.streetStem) {
      penalty += 38;
    }
    if (curObj.sideParity && c.sideParity && c.sideParity !== curObj.sideParity) {
      penalty += 18;
    }
    if (hasBearing) {
      const ang = angularDifferenceDegrees(stepBearing, Number(walkingBearingDeg));
      if (ang > 95) penalty += 65;
      else penalty += (ang / 90) * 14;
    }
    return { candidate: c, score: stepDist + penalty };
  });
  scored.sort((a, b) => a.score - b.score);
  return scored[0].candidate;
}

export function formatSurveyId(hcadOrKey, timestampMs = Date.now()) {
  const clean = String(hcadOrKey || "field")
    .replace(/[^A-Za-z0-9]+/g, "")
    .slice(0, 16) || "field";
  const ts = Math.max(0, Math.floor(Number(timestampMs) || Date.now())).toString(36);
  const rand = Math.floor(Math.random() * 1679616)
    .toString(36)
    .padStart(4, "0");
  return `SURV_${clean}_${ts}_${rand}`;
}

export function buildSurveyPhotoFilename(hcadNum, address, dateIso = "") {
  const hcad = normalizeHcad13Clean(hcadNum) || "parcel";
  const slug =
    String(address || "site")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_+|_+$/g, "")
      .slice(0, 32) || "site";
  const dt = dateIso ? new Date(dateIso) : new Date();
  const y = Number.isFinite(dt.getTime()) ? dt.getUTCFullYear() : 2026;
  const m = Number.isFinite(dt.getTime())
    ? String(dt.getUTCMonth() + 1).padStart(2, "0")
    : "10";
  const d = Number.isFinite(dt.getTime())
    ? String(dt.getUTCDate()).padStart(2, "0")
    : "30";
  return `${hcad}_${slug}_${y}${m}${d}.jpg`;
}

/**
 * Compute smart Condition + Contributing defaults from an Express Integrity Grade tile
 * so sidewalk volunteers can record a clean house survey in 2 taps (`<= 6 seconds`).
 */
export function deriveExpressSurveyDefaults(integrityGrade, inHistoricDistrict = true) {
  const grade = String(integrityGrade || "").trim();
  const outsideDefault = "Outside Historic District";
  switch (grade) {
    case "Intact":
      return {
        condition_rating: "Good",
        contributing_status: inHistoricDistrict ? "Contributing" : outsideDefault,
      };
    case "Minor Alterations":
      return {
        condition_rating: "Good",
        contributing_status: inHistoricDistrict ? "Contributing" : outsideDefault,
      };
    case "Substantially Altered":
      return {
        condition_rating: "Fair",
        contributing_status: inHistoricDistrict ? "Non-Contributing" : outsideDefault,
      };
    case "Vacant / Boarded":
      return {
        condition_rating: "Poor",
        contributing_status: inHistoricDistrict ? "Potentially Contributing" : outsideDefault,
      };
    case "Demolished / Vacant Lot":
      return {
        condition_rating: "Poor",
        contributing_status: "Vacant / Demolished",
      };
    case "Storm / Hazard Damaged":
      return {
        condition_rating: "Critical / Emergency",
        contributing_status: inHistoricDistrict ? "Contributing" : outsideDefault,
      };
    default:
      return {
        condition_rating: "Good",
        contributing_status: inHistoricDistrict ? "Contributing" : outsideDefault,
      };
  }
}

export function validateFieldSurveyDraft(draft = {}) {
  const surveyor = String(draft.surveyor_name || "").trim();
  if (!surveyor) {
    return { ok: false, field: "surveyor_name", error: "Enter Surveyor Name / Initials once to start." };
  }
  const address = String(draft.address || "").trim();
  const hcad = normalizeHcad13Clean(draft.hcad_num);
  if (!address && !hcad) {
    return { ok: false, field: "address", error: "Select a building on the map or enter its street address." };
  }
  const integrity = String(draft.integrity_grade || "").trim();
  if (!FIELD_SURVEY_INTEGRITY_GRADES.includes(integrity)) {
    return { ok: false, field: "integrity_grade", error: "Tap an Integrity Grade tile before saving." };
  }
  const condition = String(draft.condition_rating || "").trim();
  if (!FIELD_SURVEY_CONDITION_RATINGS.includes(condition)) {
    return { ok: false, field: "condition_rating", error: "Select a valid Condition Rating." };
  }
  const obsYearRaw = String(draft.observed_year ?? "").trim();
  if (obsYearRaw) {
    const yr = Number(obsYearRaw);
    if (!Number.isInteger(yr) || yr < MIN_VALID_YEAR || yr > MAX_STANDING_OVERRIDE_YEAR) {
      return {
        ok: false,
        field: "observed_year",
        error: `Observed year must be between ${MIN_VALID_YEAR} and ${MAX_STANDING_OVERRIDE_YEAR}.`,
      };
    }
  }
  const lng = Number(draft.gps_lng);
  const lat = Number(draft.gps_lat);
  if (draft.gps_lng !== "" && draft.gps_lng != null && draft.gps_lat !== "" && draft.gps_lat != null) {
    const [minLng, minLat, maxLng, maxLat] = HARRIS_COUNTY_BBOX;
    if (
      !Number.isFinite(lng) ||
      !Number.isFinite(lat) ||
      lng < minLng ||
      lng > maxLng ||
      lat < minLat ||
      lat > maxLat
    ) {
      return { ok: false, field: "gps", error: "Survey coordinates fall outside Harris County bounds." };
    }
  }
  return { ok: true, field: "", error: "" };
}

/**
 * Build GeoJSON overlay features for Field Survey Mode:
 *   - GPS accuracy circle polygon + blue fix dot
 *   - Surveyed house completion rings (synced = emerald, queued = amber)
 *   - Active house target ring
 */
export function buildFieldSurveyMapOverlayGeoJson({
  gpsFix = null,
  surveyedRecords = [],
  activeCandidate = null,
} = {}) {
  const features = [];
  if (
    gpsFix &&
    Number.isFinite(Number(gpsFix.lng)) &&
    Number.isFinite(Number(gpsFix.lat))
  ) {
    const gLng = Number(gpsFix.lng);
    const gLat = Number(gpsFix.lat);
    const accM = Math.max(4, Math.min(250, Number(gpsFix.accuracy) || 12));
    const mPerLat = METERS_PER_DEGREE_LAT;
    const mPerLon = METERS_PER_DEGREE_LAT * Math.cos(gLat * DEG_TO_RAD);
    const circleRing = [];
    const steps = 28;
    for (let i = 0; i <= steps; i += 1) {
      const a = (i / steps) * Math.PI * 2;
      circleRing.push([
        round6(gLng + (Math.cos(a) * accM) / mPerLon),
        round6(gLat + (Math.sin(a) * accM) / mPerLat),
      ]);
    }
    features.push({
      type: "Feature",
      properties: {
        overlay_kind: "gps_accuracy",
        is_low_accuracy: Number(gpsFix.accuracy) > 25 ? 1 : 0,
      },
      geometry: { type: "Polygon", coordinates: [circleRing] },
    });
    features.push({
      type: "Feature",
      properties: {
        overlay_kind: "gps_dot",
        is_low_accuracy: Number(gpsFix.accuracy) > 25 ? 1 : 0,
      },
      geometry: { type: "Point", coordinates: [round6(gLng), round6(gLat)] },
    });
  }

  const seenSurveyPts = new Set();
  for (const rec of Array.isArray(surveyedRecords) ? surveyedRecords : []) {
    let rLng = Number(rec?.gps_lng ?? rec?.target_lng);
    let rLat = Number(rec?.gps_lat ?? rec?.target_lat);
    if ((!Number.isFinite(rLng) || !Number.isFinite(rLat)) && rec?.gps_lat_lng_acc) {
      const m = String(rec.gps_lat_lng_acc).match(/(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)/);
      if (m) {
        rLat = Number(m[1]);
        rLng = Number(m[2]);
      }
    }
    if (!Number.isFinite(rLng) || !Number.isFinite(rLat)) continue;
    const sig = `${rLng.toFixed(5)},${rLat.toFixed(5)}`;
    if (seenSurveyPts.has(sig)) continue;
    seenSurveyPts.add(sig);
    features.push({
      type: "Feature",
      properties: {
        overlay_kind: "surveyed_ring",
        sync_status: String(rec.sync_status || "queued"),
      },
      geometry: { type: "Point", coordinates: [round6(rLng), round6(rLat)] },
    });
  }

  if (
    activeCandidate &&
    Number.isFinite(Number(activeCandidate.lng)) &&
    Number.isFinite(Number(activeCandidate.lat))
  ) {
    features.push({
      type: "Feature",
      properties: { overlay_kind: "active_target" },
      geometry: {
        type: "Point",
        coordinates: [round6(activeCandidate.lng), round6(activeCandidate.lat)],
      },
    });
  }

  return { type: "FeatureCollection", features };
}

// ---------------------------------------------------------------------------
// StaffWorkbenchController UI Orchestrator (Components A, B, and C)
// ---------------------------------------------------------------------------

function escHtml(val) {
  return String(val ?? "")
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function isAdminUnlockedSession(session) {
  return Boolean(session && (session.authorized || session.unlocked));
}

function getSafeBrowserStorage() {
  try {
    if (typeof window !== "undefined" && window.localStorage) {
      return window.localStorage;
    }
  } catch (_err) {
    return null;
  }
  return null;
}

export class StaffWorkbenchController {
  constructor(app, host = null) {
    this.app = app;
    this.host = host || null;
    this.mapController = app?.mapController || null;
    this.filterStore = app?.filterStore || null;

    // Component A: Historian Rapid Audit state
    this.auditQueueDoc = null;
    this.auditItems = [];
    this.auditFilteredItems = [];
    this.auditActiveIndex = 0;
    this.auditFilters = {
      category: "all",
      district: "all",
      status: "actionable",
      query: "",
    };
    this.auditMobilePane = "claim";
    this._auditLoadingPromise = null;
    this._auditBound = false;

    // Component B: Polygon Editor state
    this.polySession = null;
    this._pendingPolyEditSpec = null;
    this._openedAuthGateForPolyEdit = false;
    this._polyBound = false;
    this._prePolyBasemap = null;
    this._prePolyLayers = null;

    // Component C: Walk-the-Block Field Survey state
    this.surveyOpen = false;
    this.surveySnap = "half";
    this.surveyDaylight = false;
    this.surveyGpsEnabled = false;
    this.surveyGpsWatchId = null;
    this.surveyGpsFix = null;
    this.surveyLastStepFix = null;
    this.surveyWalkingBearingDeg = null;
    this.surveyCandidates = [];
    this.surveyActiveCandidate = null;
    this.surveyDraft = this._createBlankSurveyDraft();
    this.surveyCardDirty = false;
    this.surveyManualCondOverride = false;
    this.surveyManualContribOverride = false;
    this.surveyLastSavedId = "";
    this.surveyPhotoSummaries = [];
    this._surveyToastTimer = null;
    this._deferredSurveySyncTimers = new Map();
    this._preSurveyBasemap = null;
    this._surveyBound = false;
    this._vvHandler = null;
    this._visibilityHandler = null;

    this._globalKeyHandler = (e) => this._handleGlobalKeydown(e);
    if (typeof window !== "undefined") {
      window.addEventListener("keydown", this._globalKeyHandler);
    }
  }

  _createBlankSurveyDraft() {
    const prefs = this._loadSurveyPrefs();
    return {
      surveyor_name: prefs.surveyor_name || "",
      district: prefs.district || "",
      hcad_num: "",
      building_id: "",
      address: "",
      integrity_grade: "",
      condition_rating: "Good",
      contributing_status: "Contributing",
      window_material: "",
      cladding_material: "",
      porch_alteration: "",
      roof_alteration: "",
      observed_year: "",
      demolition_status: "",
      photo_url: "",
      photo_filename: "",
      photo_blob_saved: false,
      field_notes: "",
    };
  }

  _loadSurveyPrefs() {
    const store = getSafeBrowserStorage();
    if (!store) return { surveyor_name: "", district: "", daylight: null };
    try {
      const raw = store.getItem(LS_SURVEY_PREFS_KEY);
      if (!raw) return { surveyor_name: "", district: "", daylight: null };
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === "object"
        ? parsed
        : { surveyor_name: "", district: "", daylight: null };
    } catch (_err) {
      return { surveyor_name: "", district: "", daylight: null };
    }
  }

  _saveSurveyPrefs(patch = {}) {
    const store = getSafeBrowserStorage();
    if (!store) return;
    try {
      const next = { ...this._loadSurveyPrefs(), ...patch };
      store.setItem(LS_SURVEY_PREFS_KEY, JSON.stringify(next));
    } catch (_err) {
      // Ignore quota / Safari private errors
    }
  }

  _toast(msg) {
    if (typeof this.host?.showToast === "function") {
      this.host.showToast(msg);
    } else if (typeof this.app?.showToast === "function") {
      this.app.showToast(msg);
    }
  }

  _getWebhookUrl() {
    if (typeof this.host?.getWebhookUrl === "function") {
      return String(this.host.getWebhookUrl() || "").trim();
    }
    return String(this.mapController?.sheetSyncStatus?.webhookUrl || "").trim();
  }

  _getInspectedRawProps() {
    if (typeof this.host?.getInspectedRawProps === "function") {
      return this.host.getInspectedRawProps() || null;
    }
    return this.mapController?.selectedFeatureProps || null;
  }

  _requestCanvasRedraw() {
    if (typeof this.host?.requestCanvasRedraw === "function") {
      this.host.requestCanvasRedraw();
    } else {
      this.mapController?.requestCanvasRedraw?.();
    }
  }

  _isTextEntryFocused() {
    if (typeof document === "undefined") return false;
    const el = document.activeElement;
    if (!el) return false;
    const tag = String(el.tagName || "").toUpperCase();
    if (tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable) return true;
    if (tag === "INPUT") {
      const type = String(el.type || "text").toLowerCase();
      return !["button", "checkbox", "radio", "range", "file"].includes(type);
    }
    return false;
  }

  _isChildUtilityModalOpen() {
    if (typeof document === "undefined") return false;
    for (const id of [
      "correction-modal",
      "photo-lightbox-modal",
      "share-modal",
      "sanborn-browser-modal",
      "dossier-modal",
    ]) {
      const el = document.getElementById(id);
      if (el && !el.classList.contains("hidden")) return true;
    }
    return false;
  }

  _handleGlobalKeydown(e) {
    if (!e || e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return;
    if (this._isChildUtilityModalOpen() || this._isTextEntryFocused()) return;

    const auditModal =
      typeof document !== "undefined"
        ? document.getElementById("historian-audit-modal")
        : null;
    if (auditModal && !auditModal.classList.contains("hidden")) {
      const key = String(e.key || "").toLowerCase();
      if (key === "j") {
        e.preventDefault();
        this.stepAuditItem(1);
      } else if (key === "k") {
        e.preventDefault();
        this.stepAuditItem(-1);
      } else if (key === "a") {
        e.preventDefault();
        this.acceptCurrentAuditItem();
      } else if (key === "d") {
        e.preventDefault();
        this.dismissCurrentAuditItem();
      } else if (key === "s") {
        e.preventDefault();
        this.skipCurrentAuditItem();
      }
      return;
    }

    if (this.polySession) {
      if (e.key === "Delete" || e.key === "Backspace") {
        if (this.polySession.activeVertexIndex >= 0) {
          e.preventDefault();
          this.deleteActivePolygonVertex();
        }
      }
    }
  }

  onAdminAuthChanged(isUnlocked) {
    if (isUnlocked && this._pendingPolyEditSpec) {
      const pending = this._pendingPolyEditSpec;
      this._pendingPolyEditSpec = null;
      if (this._openedAuthGateForPolyEdit) {
        this._openedAuthGateForPolyEdit = false;
        document.getElementById("correction-modal")?.classList.add("hidden");
      }
      this.openPolygonEditor(pending);
      return;
    }
    if (!isUnlocked && this.polySession) {
      this.teardownPolyEditSession({ updateUrl: true, restoreBasemap: false });
      this._toast("Staff Admin locked; closed Polygon Editor.");
    }
    const auditModal =
      typeof document !== "undefined"
        ? document.getElementById("historian-audit-modal")
        : null;
    if (auditModal && !auditModal.classList.contains("hidden")) {
      this._renderActiveAuditItem();
    }
  }

  cancelPendingPolyEditAuthGate({ updateUrl = true } = {}) {
    const hadGate = Boolean(this._pendingPolyEditSpec || this._openedAuthGateForPolyEdit);
    this._pendingPolyEditSpec = null;
    this._openedAuthGateForPolyEdit = false;
    if (hadGate && updateUrl && this.filterStore?.getState?.()?.activePolyEdit) {
      this.filterStore.setState({ activePolyEdit: null });
    }
  }

  refreshAfterOverrideChange() {
    const auditModal =
      typeof document !== "undefined"
        ? document.getElementById("historian-audit-modal")
        : null;
    if (auditModal && !auditModal.classList.contains("hidden")) {
      this._applyAuditFiltersAndRender({ preserveCurrentFid: true });
    }
  }

  // -------------------------------------------------------------------------
  // Component A: Split-Screen Historian Rapid Audit Mode (`#4`)
  // -------------------------------------------------------------------------

  async ensureAuditQueueLoaded() {
    if (this.auditQueueDoc && this.auditItems.length > 0) {
      return this.auditItems;
    }
    if (this._auditLoadingPromise) return this._auditLoadingPromise;
    this._auditLoadingPromise = (async () => {
      const resp = await fetch("public/data/historical_audit_queue.json?v=20261030e");
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status} loading historical_audit_queue.json`);
      }
      const doc = await resp.json();
      this.auditQueueDoc = doc;
      this.auditItems = hydrateAuditQueuePayload(doc);
      return this.auditItems;
    })();
    try {
      return await this._auditLoadingPromise;
    } catch (err) {
      this._auditLoadingPromise = null;
      throw err;
    }
  }

  _bindAuditDomOnce() {
    if (this._auditBound || typeof document === "undefined") return;
    const modal = document.getElementById("historian-audit-modal");
    if (!modal) return;
    this._auditBound = true;

    document.getElementById("btn-close-audit-modal")?.addEventListener("click", () => {
      this.closeHistorianAuditModal();
    });
    modal.addEventListener("click", (e) => {
      if (e.target === modal) this.closeHistorianAuditModal();
    });

    document.getElementById("btn-audit-prev")?.addEventListener("click", () => {
      this.stepAuditItem(-1);
    });
    document.getElementById("btn-audit-next")?.addEventListener("click", () => {
      this.stepAuditItem(1);
    });

    const onFilterMutate = () => {
      this.auditFilters.category =
        document.getElementById("audit-filter-category")?.value || "all";
      this.auditFilters.district =
        document.getElementById("audit-filter-district")?.value || "all";
      this.auditFilters.status =
        document.getElementById("audit-filter-status")?.value || "all";
      this.auditFilters.query =
        document.getElementById("audit-filter-search")?.value || "";
      this._applyAuditFiltersAndRender({ preserveCurrentFid: false });
    };
    document.getElementById("audit-filter-category")?.addEventListener("change", onFilterMutate);
    document.getElementById("audit-filter-district")?.addEventListener("change", onFilterMutate);
    document.getElementById("audit-filter-status")?.addEventListener("change", onFilterMutate);
    document.getElementById("audit-filter-search")?.addEventListener("input", onFilterMutate);
    document.getElementById("audit-btn-reset-filters")?.addEventListener("click", () => {
      this.auditFilters = { category: "all", district: "all", status: "all", query: "" };
      const catEl = document.getElementById("audit-filter-category");
      const distEl = document.getElementById("audit-filter-district");
      const stEl = document.getElementById("audit-filter-status");
      const qEl = document.getElementById("audit-filter-search");
      if (catEl) catEl.value = "all";
      if (distEl) distEl.value = "all";
      if (stEl) stEl.value = "all";
      if (qEl) qEl.value = "";
      this._applyAuditFiltersAndRender({ preserveCurrentFid: false });
    });

    document.getElementById("audit-mobile-tab-claim")?.addEventListener("click", () => {
      this._setAuditMobilePane("claim");
    });
    document.getElementById("audit-mobile-tab-archival")?.addEventListener("click", () => {
      this._setAuditMobilePane("archival");
    });
  }

  _setAuditMobilePane(pane) {
    this.auditMobilePane = pane === "archival" ? "archival" : "claim";
    const modal = document.getElementById("historian-audit-modal");
    if (modal) modal.setAttribute("data-mobile-pane", this.auditMobilePane);
    const btnClaim = document.getElementById("audit-mobile-tab-claim");
    const btnArch = document.getElementById("audit-mobile-tab-archival");
    if (btnClaim) {
      const active = this.auditMobilePane === "claim";
      btnClaim.classList.toggle("active", active);
      btnClaim.setAttribute("aria-selected", active ? "true" : "false");
    }
    if (btnArch) {
      const active = this.auditMobilePane === "archival";
      btnArch.classList.toggle("active", active);
      btnArch.setAttribute("aria-selected", active ? "true" : "false");
    }
  }

  _populateAuditFilterOptions() {
    const catSelect = document.getElementById("audit-filter-category");
    const distSelect = document.getElementById("audit-filter-district");
    const statusSelect = document.getElementById("audit-filter-status");
    if (catSelect && catSelect.options.length <= 1) {
      const catCounts = new Map();
      for (const it of this.auditItems) {
        catCounts.set(it.category, (catCounts.get(it.category) || 0) + 1);
      }
      catSelect.innerHTML = `<option value="all">All Categories (${this.auditItems.length.toLocaleString()})</option>`;
      for (const [cat, count] of catCounts.entries()) {
        const label = AUDIT_CATEGORY_LABELS[cat] || cat;
        catSelect.insertAdjacentHTML(
          "beforeend",
          `<option value="${escHtml(cat)}">${escHtml(label)} (${count.toLocaleString()})</option>`
        );
      }
      catSelect.value = this.auditFilters.category || "all";
    }
    if (distSelect && distSelect.options.length <= 1) {
      const distCounts = new Map();
      let designatedTotal = 0;
      for (const it of this.auditItems) {
        const d = it.district || AUDIT_UNDESIGNATED_DISTRICT;
        distCounts.set(d, (distCounts.get(d) || 0) + 1);
        if (d !== AUDIT_UNDESIGNATED_DISTRICT) designatedTotal += 1;
      }
      const undesignatedCount = distCounts.get(AUDIT_UNDESIGNATED_DISTRICT) || 0;
      const namedDists = Array.from(distCounts.keys())
        .filter((d) => d !== AUDIT_UNDESIGNATED_DISTRICT)
        .sort((a, b) => a.localeCompare(b));

      let html = `<option value="all">All Districts &amp; Undesignated (${this.auditItems.length.toLocaleString()})</option>`;
      html += `<option value="designated_only">Designated COH Historic Districts Only (${designatedTotal.toLocaleString()})</option>`;
      html += `<option value="${escHtml(AUDIT_UNDESIGNATED_DISTRICT)}">${escHtml(
        AUDIT_UNDESIGNATED_DISTRICT
      )} (${undesignatedCount.toLocaleString()})</option>`;
      for (const d of namedDists) {
        html += `<option value="${escHtml(d)}">${escHtml(d)} (${(
          distCounts.get(d) || 0
        ).toLocaleString()})</option>`;
      }
      distSelect.innerHTML = html;
      distSelect.value = this.auditFilters.district || "all";
    }
    if (statusSelect) {
      statusSelect.value = this.auditFilters.status || "all";
    }
  }

  async openHistorianAuditModal({
    targetFid = "",
    hcadNum = "",
    auditCategory = "",
    updateUrl = true,
  } = {}) {
    if (typeof document === "undefined") return;
    this._bindAuditDomOnce();
    const modal = document.getElementById("historian-audit-modal");
    if (!modal) return;
    modal.classList.remove("hidden");
    this._setAuditMobilePane("claim");

    const claimHost = document.getElementById("audit-claim-card-host");
    if (claimHost && this.auditItems.length === 0) {
      claimHost.innerHTML = `<div class="audit-loading-state">Loading 2,600 archival cross-check findings...</div>`;
    }

    try {
      await Promise.all([
        this.ensureAuditQueueLoaded(),
        this.app?.ensureArchivalCatalogsLoaded?.() || Promise.resolve(),
      ]);
    } catch (err) {
      if (claimHost) {
        claimHost.innerHTML = `<div class="audit-empty-state">Unable to load historical audit queue: ${escHtml(
          err?.message || String(err)
        )}</div>`;
      }
      return;
    }

    const cleanAuditCat =
      auditCategory && auditCategory !== "all"
        ? String(auditCategory).trim().toUpperCase()
        : "";
    if (cleanAuditCat && Object.prototype.hasOwnProperty.call(AUDIT_CATEGORY_LABELS, cleanAuditCat)) {
      this.auditFilters.category = cleanAuditCat;
    }

    this._populateAuditFilterOptions();
    if (cleanAuditCat && Object.prototype.hasOwnProperty.call(AUDIT_CATEGORY_LABELS, cleanAuditCat)) {
      const catEl = document.getElementById("audit-filter-category");
      if (catEl) catEl.value = cleanAuditCat;
    }

    const cleanTargetFid =
      targetFid && targetFid !== "1" && targetFid !== "true"
        ? String(targetFid).trim()
        : "";
    const cleanTargetHcad = normalizeHcad13Clean(hcadNum || cleanTargetFid);

    if (cleanTargetFid || cleanTargetHcad) {
      // Ensure filters don't hide a deep-linked item
      const inCurrent = filterAuditQueueItems(
        this.auditItems,
        this.auditFilters,
        loadAuditDecisions(),
        this.mapController?.curatedOverrides || {}
      );
      const matchesFiltered = inCurrent.some(
        (it) =>
          (cleanTargetFid && it.fid === cleanTargetFid) ||
          (cleanTargetHcad &&
            it.hcad_num === cleanTargetHcad &&
            (!cleanAuditCat || it.category === cleanAuditCat))
      );
      if (!matchesFiltered) {
        const keepCat =
          cleanAuditCat &&
          this.auditItems.some(
            (it) =>
              ((cleanTargetFid && it.fid === cleanTargetFid) ||
                (cleanTargetHcad && it.hcad_num === cleanTargetHcad)) &&
              it.category === cleanAuditCat
          )
            ? cleanAuditCat
            : "all";
        this.auditFilters = { category: keepCat, district: "all", status: "all", query: "" };
        const catEl = document.getElementById("audit-filter-category");
        const distEl = document.getElementById("audit-filter-district");
        const stEl = document.getElementById("audit-filter-status");
        const qEl = document.getElementById("audit-filter-search");
        if (catEl) catEl.value = keepCat;
        if (distEl) distEl.value = "all";
        if (stEl) stEl.value = "all";
        if (qEl) qEl.value = "";
      }
    }

    this._applyAuditFiltersAndRender({
      targetFid: cleanTargetFid,
      targetHcad: cleanTargetHcad,
      targetCategory: cleanAuditCat,
      preserveCurrentFid: !cleanTargetFid && !cleanTargetHcad && !cleanAuditCat,
      syncUrl: updateUrl,
    });
  }

  closeHistorianAuditModal({ updateUrl = true } = {}) {
    if (typeof document === "undefined") return;
    const modal = document.getElementById("historian-audit-modal");
    if (modal) modal.classList.add("hidden");
    if (updateUrl && this.filterStore) {
      const curState = this.filterStore.getState();
      if (curState.activeAudit || (curState.activeAuditCategory && curState.activeAuditCategory !== "all")) {
        this.filterStore.setState({ activeAudit: false, activeAuditCategory: "all" });
      }
    }
  }

  _syncAuditUrlState() {
    if (!this.filterStore) return;
    const modal = document.getElementById("historian-audit-modal");
    if (!modal || modal.classList.contains("hidden")) return;
    const activeItem = this.auditFilteredItems[this.auditActiveIndex] || null;
    const nextVal = activeItem?.fid || true;
    const nextCat = this.auditFilters.category || "all";
    const curState = this.filterStore.getState();
    if (curState.activeAudit !== nextVal || (curState.activeAuditCategory || "all") !== nextCat) {
      this.filterStore.setState({
        activeAudit: nextVal,
        activeAuditCategory: nextCat,
      });
    }
  }

  _applyAuditFiltersAndRender({
    targetFid = "",
    targetHcad = "",
    targetCategory = "",
    preserveCurrentFid = true,
    syncUrl = true,
  } = {}) {
    const decisions = loadAuditDecisions();
    const overrides = this.mapController?.curatedOverrides || {};
    const prevFid =
      preserveCurrentFid && this.auditFilteredItems[this.auditActiveIndex]
        ? this.auditFilteredItems[this.auditActiveIndex].fid
        : "";

    this.auditFilteredItems = filterAuditQueueItems(
      this.auditItems,
      this.auditFilters,
      decisions,
      overrides
    );

    let nextIdx = 0;
    let matched = false;
    const wantFid = targetFid || prevFid;
    if (wantFid) {
      const found = this.auditFilteredItems.findIndex((it) => it.fid === wantFid);
      if (found >= 0) {
        nextIdx = found;
        matched = true;
      }
    }
    if (!matched && targetHcad) {
      let foundH = -1;
      if (targetCategory) {
        foundH = this.auditFilteredItems.findIndex(
          (it) => it.hcad_num === targetHcad && it.category === targetCategory
        );
      }
      if (foundH < 0) {
        foundH = this.auditFilteredItems.findIndex((it) => it.hcad_num === targetHcad);
      }
      if (foundH >= 0) nextIdx = foundH;
    }
    this.auditActiveIndex = Math.max(
      0,
      Math.min(nextIdx, Math.max(0, this.auditFilteredItems.length - 1))
    );

    const stats = computeAuditProgressStats(this.auditItems, decisions, overrides);
    const progText = document.getElementById("audit-progress-text");
    const progFill = document.getElementById("audit-progress-fill");
    if (progText) {
      progText.textContent =
        `${stats.completed.toLocaleString()} / ${stats.total.toLocaleString()} triaged ` +
        `(${stats.percentComplete}% | ${stats.pending.toLocaleString()} actionable)`;
    }
    if (progFill) {
      progFill.style.width = `${Math.min(100, Math.max(0, stats.percentComplete))}%`;
    }

    this._renderActiveAuditItem();
    if (syncUrl) this._syncAuditUrlState();
  }

  stepAuditItem(delta) {
    if (!this.auditFilteredItems.length) return;
    const len = this.auditFilteredItems.length;
    this.auditActiveIndex = (((this.auditActiveIndex + delta) % len) + len) % len;
    this._renderActiveAuditItem();
    this._syncAuditUrlState();
  }

  _advanceToNextPendingAfterTriage(currentFid) {
    const decisions = loadAuditDecisions();
    const overrides = this.mapController?.curatedOverrides || {};
    const curPos = this.auditActiveIndex;
    this.auditFilteredItems = filterAuditQueueItems(
      this.auditItems,
      this.auditFilters,
      decisions,
      overrides
    );
    if (this.auditFilteredItems.length === 0) {
      this.auditActiveIndex = 0;
      this._applyAuditFiltersAndRender({ preserveCurrentFid: false });
      return;
    }
    const stillIdx = this.auditFilteredItems.findIndex((it) => it.fid === currentFid);
    let candidateIdx = stillIdx >= 0 ? (stillIdx + 1) % this.auditFilteredItems.length : Math.min(curPos, this.auditFilteredItems.length - 1);
    for (let offset = 0; offset < this.auditFilteredItems.length; offset += 1) {
      const probe = (candidateIdx + offset) % this.auditFilteredItems.length;
      const st = resolveAuditItemStatus(this.auditFilteredItems[probe], decisions, overrides);
      if (st === "pending") {
        candidateIdx = probe;
        break;
      }
    }
    this.auditActiveIndex = candidateIdx;
    const stats = computeAuditProgressStats(this.auditItems, decisions, overrides);
    const progText = document.getElementById("audit-progress-text");
    const progFill = document.getElementById("audit-progress-fill");
    if (progText) {
      progText.textContent =
        `${stats.completed.toLocaleString()} / ${stats.total.toLocaleString()} triaged ` +
        `(${stats.percentComplete}% | ${stats.pending.toLocaleString()} actionable)`;
    }
    if (progFill) {
      progFill.style.width = `${Math.min(100, Math.max(0, stats.percentComplete))}%`;
    }
    this._renderActiveAuditItem();
    this._syncAuditUrlState();
  }

  _buildAuditItemPropsForCorrection(item) {
    return {
      hcad_num: item.hcad_num,
      building_id: item.building_id || "",
      address: item.address,
      building_name: item.building_name || "",
      year_built: item.atlas_year || item.hcad_year || "",
      historic_district:
        item.district && item.district !== AUDIT_UNDESIGNATED_DISTRICT ? item.district : "",
      contributing: item.contributing || "",
      architect: item.architect || "",
      source_type: item.source_type || "",
      source_url: item.source_url || "",
      source_citation: item.conflicting_evidence || "",
      lng: item.lng,
      lat: item.lat,
    };
  }

  async acceptCurrentAuditItem() {
    const item = this.auditFilteredItems[this.auditActiveIndex];
    if (!item) return;
    const yearEl = document.getElementById("audit-input-year");
    const noteEl = document.getElementById("audit-input-note");
    const srcEl = document.getElementById("audit-select-source-type");
    const addrEl = document.getElementById("audit-input-address");

    const admin = getAdminSession();
    const isAdmin = isAdminUnlockedSession(admin);
    const maxAllowedYear = isAdmin ? MAX_STANDING_OVERRIDE_YEAR : MAX_VALID_YEAR;
    const rawYr = String(yearEl?.value || "").trim();
    const yrNum = rawYr ? Number(rawYr) : null;
    if (
      rawYr &&
      (!Number.isInteger(yrNum) || yrNum < MIN_VALID_YEAR || yrNum > maxAllowedYear)
    ) {
      this._toast(`Year must be between ${MIN_VALID_YEAR} and ${maxAllowedYear}.`);
      yearEl?.focus?.();
      return;
    }

    const customNote = String(noteEl?.value || "").trim();
    const sourceType = String(srcEl?.value || item.source_type || "Houston City Directories (1866-1926)").trim();
    const typedAddress = String(addrEl?.value || item.address || "").trim();
    const nextAddress =
      typedAddress || (item.hcad_num ? `HCAD ${item.hcad_num}` : String(item.fid || "Unknown Property"));
    const isSuspectedDemo = item.category === "SUSPECTED_UNMARKED_LANDMARK_DEMOLITION";
    const citation = [
      `[HISTORIAN AUDIT ${item.fid}]`,
      item.conflicting_evidence,
      customNote,
    ]
      .filter(Boolean)
      .join(" | ");

    const payload = {
      hcad_num: item.building_id || item.hcad_num,
      address: nextAddress,
      building_name: item.building_name || "",
      historic_district:
        item.district && item.district !== AUDIT_UNDESIGNATED_DISTRICT ? item.district : "",
      contributing: item.contributing || "",
      current_year_built: item.atlas_year || item.hcad_year || "",
      suggested_year_built: isSuspectedDemo ? "0" : yrNum || "",
      suppress_only: isSuspectedDemo,
      architect: item.architect || "",
      source_type: sourceType,
      source_url: item.source_url || "",
      source_citation: citation,
      footprint_notes: customNote,
      footprint_issue: isSuspectedDemo
        ? "Building demolished - lot currently vacant"
        : `Audit reconciliation (${item.category_label})`,
    };

    if (!isSuspectedDemo && !yrNum && typedAddress === String(item.address || "").trim() && !customNote) {
      // Nothing concrete supplied directly: open full correction modal prefilled so historian specifies fix
      this.app?.openCorrectionModal?.(this._buildAuditItemPropsForCorrection(item));
      this._toast("Enter proposed year, address, or note to publish this audit reconciliation.");
      return;
    }

    const webhookUrl = this._getWebhookUrl();
    const statusBanner = document.getElementById("audit-action-feedback");
    if (statusBanner) {
      statusBanner.textContent = isAdmin
        ? "Publishing staff override..."
        : "Submitting archival suggestion to moderation queue...";
      statusBanner.classList.remove("hidden");
    }

    if (isAdmin) {
      let res = null;
      let remoteError = "";
      try {
        res = await submitAdminApprovedOverride(payload, webhookUrl);
      } catch (err) {
        remoteError = err?.message || String(err);
        const isOfflineErr =
          (typeof navigator !== "undefined" && navigator.onLine === false) ||
          err?.name === "TypeError" ||
          /failed to fetch|networkerror|load failed|network request failed/i.test(remoteError);
        if (!isOfflineErr) {
          if (statusBanner) {
            statusBanner.textContent = `Admin publish failed: ${remoteError}`;
            statusBanner.classList.remove("hidden");
          }
          this._toast(`Admin publish rejected: ${remoteError}`);
          return;
        }
        try {
          res = await submitAdminApprovedOverride(payload, "");
        } catch (_localErr) {
          res = null;
        }
      }
      const ovKey = String(payload.hcad_num || item.hcad_num).trim();
      const livePatch =
        res?.override ||
        (isSuspectedDemo
          ? {
              hcad_num: normalizeHcad13Clean(item.hcad_num),
              address: nextAddress,
              source_type: sourceType,
              source_url: item.source_url || "",
              source_citation: citation,
              suppress_only: true,
              replace_parcel_shards: !ovKey.includes("#"),
            }
          : {
              hcad_num: normalizeHcad13Clean(item.hcad_num),
              address: nextAddress,
              building_name: item.building_name || undefined,
              year_built: yrNum || undefined,
              decade: yrNum ? Math.floor(yrNum / 10) * 10 : undefined,
              source_type: sourceType,
              source_url: item.source_url || "",
              source_citation: citation,
              keep_shard_footprints: true,
            });
      this.app?.applyLiveCuratedOverrideMapUpdate?.(ovKey, livePatch, null);
      saveAuditDecision(item.fid, "accepted", customNote || `Approved ${yrNum || "update"}`);
      this._toast(
        res?.webhookDelivered
          ? `Approved & published ${item.address || item.hcad_num} to Google Sheet.`
          : `Applied live override for ${item.address || item.hcad_num} (${
              remoteError || "webhook offline / not configured"
            }).`
      );
      this._advanceToNextPendingAfterTriage(item.fid);
    } else {
      let res = null;
      try {
        res = await submitCorrectionSuggestion(payload, webhookUrl);
      } catch (err) {
        const msg = err?.message || String(err);
        if (statusBanner) {
          statusBanner.textContent = `Submission rejected: ${msg}`;
          statusBanner.classList.remove("hidden");
        }
        this._toast(`Could not submit correction: ${msg}`);
        return;
      }
      saveAuditDecision(item.fid, "accepted", customNote || "Queued community suggestion");
      this._toast(
        res?.webhookDelivered
          ? `Submitted archival correction for ${item.address || item.hcad_num}.`
          : `Saved suggestion locally for ${item.address || item.hcad_num} (export CSV or unlock Admin for live publish).`
      );
      this._advanceToNextPendingAfterTriage(item.fid);
    }
  }

  dismissCurrentAuditItem() {
    const item = this.auditFilteredItems[this.auditActiveIndex];
    if (!item) return;
    const note = String(document.getElementById("audit-input-note")?.value || "").trim();
    saveAuditDecision(item.fid, "dismissed", note || "Verified current Atlas record");
    this._toast(`Dismissed finding on ${item.address || item.hcad_num}.`);
    this._advanceToNextPendingAfterTriage(item.fid);
  }

  skipCurrentAuditItem() {
    const item = this.auditFilteredItems[this.auditActiveIndex];
    if (!item) return;
    saveAuditDecision(item.fid, "skipped", "");
    this._advanceToNextPendingAfterTriage(item.fid);
  }

  _syncMapViewportToAuditItem(item) {
    if (!item || !this.mapController) return;
    const lng = Number(item.lng);
    const lat = Number(item.lat);
    if (!Number.isFinite(lng) || !Number.isFinite(lat)) return;
    try {
      if (this.mapController.useCanvasFallback && this.mapController.canvasState) {
        this.mapController.canvasState.lng = lng;
        this.mapController.canvasState.lat = lat;
        this.mapController.canvasState.zoom = Math.max(
          Number(this.mapController.canvasState.zoom) || 0,
          19.5
        );
        this.mapController.canvasState.pitch = 0;
        this._requestCanvasRedraw();
      } else if (this.mapController.map?.jumpTo) {
        const curZ = Number(this.mapController.map.getZoom?.()) || 0;
        this.mapController.map.jumpTo({
          center: [lng, lat],
          zoom: Math.max(curZ, 19.5),
          pitch: 0,
        });
      }
    } catch (_err) {
      // Ignore camera sync failures on teardown or mock controllers
    }
  }

  _renderActiveAuditItem() {
    const navCounter = document.getElementById("audit-nav-counter");
    const claimHost = document.getElementById("audit-claim-card-host");
    const archHost = document.getElementById("audit-archival-pane-host");
    if (!claimHost || !archHost) return;

    if (!this.auditFilteredItems.length) {
      if (navCounter) navCounter.textContent = "0 / 0";
      claimHost.innerHTML = `<div class="audit-empty-state">
        <h4>No findings match current filters</h4>
        <p>Reset category, district, status, or search filters to continue triaging contradictions.</p>
      </div>`;
      archHost.innerHTML = `<div class="audit-empty-state">Select a finding to inspect City Directory OCR &amp; Sanborn sheets.</div>`;
      return;
    }

    const item = this.auditFilteredItems[this.auditActiveIndex];
    this._syncMapViewportToAuditItem(item);
    const decisions = loadAuditDecisions();
    const overrides = this.mapController?.curatedOverrides || {};
    const status = resolveAuditItemStatus(item, decisions, overrides);
    const admin = getAdminSession();
    if (navCounter) {
      navCounter.textContent = `${(this.auditActiveIndex + 1).toLocaleString()} / ${this.auditFilteredItems.length.toLocaleString()}`;
    }

    const satSpec = buildSatellitePreviewTileSpec(item.lng, item.lat, {
      displayZoom: 19.4,
      nativeMaxZoom: 18,
      viewportPx: 210,
    });
    const gridW = 768;
    const centerPxX = 256 + satSpec.offsetWithinTileX;
    const centerPxY = 256 + satSpec.offsetWithinTileY;
    const tx = satSpec.viewportPx * 0.5 - centerPxX * satSpec.scale;
    const ty = satSpec.viewportPx * 0.5 - centerPxY * satSpec.scale;
    const satTilesHtml = satSpec.tiles
      .map(
        (t) =>
          `<img src="${escHtml(t.url)}" alt="" loading="eager" decoding="async" ` +
          `style="grid-column:${t.gridCol};grid-row:${t.gridRow};width:256px;height:256px;display:block;" />`
      )
      .join("");

    const statusBadgeMap = {
      pending: { cls: "pending", text: "Actionable" },
      accepted: { cls: "accepted", text: "Accepted" },
      resolved_override: { cls: "accepted", text: "Resolved by Live Override" },
      dismissed: { cls: "dismissed", text: "Dismissed (False Positive)" },
      skipped: { cls: "skipped", text: "Skipped" },
    };
    const stInfo = statusBadgeMap[status] || statusBadgeMap.pending;
    const savedNote = decisions[item.fid]?.note || "";

    claimHost.innerHTML = `
      <div class="audit-card-header">
        <div class="audit-eyebrow-pills">
          <span class="audit-pill priority">P${escHtml(item.priority)}</span>
          <span class="audit-pill category">${escHtml(item.category_label)}</span>
          <span class="audit-pill district">${escHtml(item.district)}</span>
          <span class="audit-pill status-${escHtml(stInfo.cls)}" id="audit-current-status-pill">${escHtml(
            stInfo.text
          )}</span>
        </div>
        <h3 class="audit-item-title">${escHtml(
          item.building_name ? `${item.building_name} - ${item.address}` : item.address || `HCAD ${item.hcad_num}`
        )}</h3>
        <div class="audit-item-submeta">
          <span>HCAD <code>${escHtml(item.hcad_num)}</code></span>
          ${item.building_id ? `<span>Footprint <code>${escHtml(item.building_id)}</code></span>` : ""}
          ${item.contributing ? `<span>${escHtml(item.contributing)}</span>` : ""}
          ${item.architect ? `<span>Architect: ${escHtml(item.architect)}</span>` : ""}
        </div>
      </div>

      <div class="audit-roof-and-claims">
        <div class="audit-sat-preview-card">
          <div class="audit-sat-viewport" style="width:${satSpec.viewportPx}px;height:${satSpec.viewportPx}px;" title="ArcGIS World Imagery roof crop (native z=${satSpec.tileZ} scaled to z=${satSpec.displayZoom})">
            <div class="audit-sat-tile-grid" style="width:${gridW}px;height:${gridW}px;transform:translate(${tx.toFixed(
              1
            )}px, ${ty.toFixed(1)}px) scale(${satSpec.scale});transform-origin:0 0;">
              ${satTilesHtml}
            </div>
            <div class="audit-sat-reticle" aria-hidden="true"></div>
          </div>
          <div class="audit-sat-actions">
            <button type="button" class="audit-btn secondary" id="btn-audit-focus-map">
              Focus Main Map (z=19.8)
            </button>
            <button type="button" class="audit-btn secondary" id="btn-audit-edit-polygon">
              Reshape / Trace Footprint
            </button>
          </div>
        </div>

        <div class="audit-comparison-stack">
          <div class="audit-compare-box claim">
            <div class="audit-compare-label">Displayed Atlas / HCAD Claim</div>
            <div class="audit-compare-value">${escHtml(
              item.displayed_claim ||
                (item.atlas_year ? `Atlas year ${item.atlas_year}` : "Current Atlas record")
            )}</div>
            <div class="audit-compare-years">
              <span>Atlas Year: <strong>${item.atlas_year ?? "Unknown"}</strong></span>
              <span>HCAD Roll: <strong>${item.hcad_year ?? "Unknown"}</strong></span>
            </div>
          </div>
          <div class="audit-compare-box evidence">
            <div class="audit-compare-label">Conflicting Archival Evidence</div>
            <div class="audit-compare-value">${escHtml(item.conflicting_evidence)}</div>
            ${
              item.recommended_action
                ? `<div class="audit-recommend-note">${escHtml(item.recommended_action)}</div>`
                : ""
            }
          </div>
        </div>
      </div>

      <div class="audit-reconcile-form">
        <div class="audit-form-grid">
          <label class="audit-field">
            <span>Proposed Year Built</span>
            <input
              type="number"
              id="audit-input-year"
              min="${MIN_VALID_YEAR}"
              max="${MAX_STANDING_OVERRIDE_YEAR}"
              value="${item.suggested_year ?? ""}"
              placeholder="${item.atlas_year ?? "e.g. 1912"}"
            />
          </label>
          <label class="audit-field">
            <span>Verified Street Address</span>
            <input
              type="text"
              id="audit-input-address"
              value="${escHtml(item.address)}"
              placeholder="Street address"
            />
          </label>
          <label class="audit-field">
            <span>Primary Evidence Source</span>
            <select id="audit-select-source-type">
              ${[
                item.source_type,
                "Houston City Directories (1866-1926)",
                "Sanborn Fire Insurance Map",
                "City of Houston Protected Landmark / Archaeological Site",
                "City of Houston Historic District Inventory",
                "National Register Nomination",
                "Field Survey / Architectural Guide",
              ]
                .filter((v, i, arr) => v && arr.indexOf(v) === i)
                .map((opt) => `<option value="${escHtml(opt)}">${escHtml(opt)}</option>`)
                .join("")}
            </select>
          </label>
        </div>
        <label class="audit-field full">
          <span>Historian Verification Note (saved to citation &amp; local triage log)</span>
          <input
            type="text"
            id="audit-input-note"
            value="${escHtml(savedNote)}"
            placeholder="e.g. Confirmed footprint on 1907 Sanborn Vol 2 Sheet 44; garage moved rear"
          />
        </label>
      </div>

      <div class="audit-action-feedback hidden" id="audit-action-feedback" role="status" aria-live="polite"></div>

      <div class="audit-action-bar">
        <button type="button" class="audit-btn accept" id="btn-audit-accept" title="Keyboard shortcut: A">
          ${
            isAdminUnlockedSession(admin)
              ? `Approve &amp; Publish Override${item.suggested_year ? ` (${item.suggested_year})` : ""} [A]`
              : `Suggest Archival Correction${item.suggested_year ? ` (${item.suggested_year})` : ""} [A]`
          }
        </button>
        <button type="button" class="audit-btn dismiss" id="btn-audit-dismiss" title="Keyboard shortcut: D">
          Dismiss False Positive [D]
        </button>
        <button type="button" class="audit-btn secondary" id="btn-audit-skip" title="Keyboard shortcut: S">
          Skip [S]
        </button>
        <button type="button" class="audit-btn secondary" id="btn-audit-open-correction">
          Full Correction Modal...
        </button>
      </div>
    `;

    claimHost.querySelector("#btn-audit-accept")?.addEventListener("click", () => {
      this.acceptCurrentAuditItem();
    });
    claimHost.querySelector("#btn-audit-dismiss")?.addEventListener("click", () => {
      this.dismissCurrentAuditItem();
    });
    claimHost.querySelector("#btn-audit-skip")?.addEventListener("click", () => {
      this.skipCurrentAuditItem();
    });
    claimHost.querySelector("#btn-audit-open-correction")?.addEventListener("click", () => {
      this.app?.openCorrectionModal?.(this._buildAuditItemPropsForCorrection(item));
    });
    claimHost.querySelector("#btn-audit-focus-map")?.addEventListener("click", () => {
      this.closeHistorianAuditModal({ updateUrl: false });
      this.filterStore?.setState?.({ basemap: "satellite", extrude3D: false });
      this.mapController?.flyToLocation?.({
        lng: item.lng,
        lat: item.lat,
        zoom: 19.8,
        pitch: 0,
      });
      this.mapController?.selectFeatureByIdOrHcad?.({
        hcadNum: item.hcad_num,
        featureId: item.building_id || "",
        addressQuery: item.address,
        targetLng: item.lng,
        targetLat: item.lat,
        flyTo: false,
      });
      this._toast(`Focused ${item.address || item.hcad_num} at z=19.8 over satellite imagery.`);
    });
    claimHost.querySelector("#btn-audit-edit-polygon")?.addEventListener("click", () => {
      this.closeHistorianAuditModal({ updateUrl: false });
      this.openPolygonEditor({
        targetKey: item.building_id || item.hcad_num,
        hcadNum: item.hcad_num,
        props: this._buildAuditItemPropsForCorrection(item),
      });
    });

    // Right Pane: Deep Archival Verification (`resolveAuditItemArchivalContext`)
    const arch = resolveAuditItemArchivalContext(item, {
      citydirIndex: this.app?.citydirIndex || null,
      sanbornCatalog: this.app?.sanbornCatalog || null,
    });
    const cd = arch.citydir;
    const gsvUrl = `https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${item.lat},${item.lng}`;
    const hcadGisUrl = item.hcad_num
      ? `https://arcweb.hcad.org/parcel-viewer-v2.0/?hcad_num=${encodeURIComponent(item.hcad_num)}`
      : "https://search.hcad.org/";

    const aliasHtml =
      cd.streetAliases.length > 0
        ? `<div class="audit-street-alias-callout">
            <div class="audit-alias-heading">Historic Street Name Alert (${escHtml(arch.streetStem)})</div>
            ${cd.streetAliases
              .map(
                (al) => `
              <div class="audit-alias-row">
                <span>Listed as <strong>${escHtml(al.former)}</strong> (${escHtml(al.era)}) - ${escHtml(
                  al.note
                )}</span>
                <a href="${escHtml(al.searchUrl)}" target="_blank" rel="noopener noreferrer" class="audit-link-chip">
                  Search "${escHtml(al.searchQuery)}" &#8599;
                </a>
              </div>`
              )
              .join("")}
          </div>`
        : "";

    const sanbornBoundariesHtml =
      arch.sanborn.matchedBoundaries.length > 0
        ? arch.sanborn.matchedBoundaries
            .map(
              (b) => `
            <div class="audit-sanborn-boundary-row">
              <div>
                <strong>${escHtml(b.volYear || b.era)}</strong> - ${escHtml(b.title)}
                ${b.notes ? `<div class="audit-muted-small">${escHtml(b.notes)}</div>` : ""}
              </div>
              ${
                b.imageUrl
                  ? `<a href="${escHtml(b.imageUrl)}" target="_blank" rel="noopener noreferrer" class="audit-link-chip">Index Map &#8599;</a>`
                  : ""
              }
            </div>`
            )
            .join("")
        : `<div class="audit-muted-small">Parcel sits outside digitized 1877-1955 Sanborn index polygons (check post-1925 annexations or county topographic quads).</div>`;

    const sanbornVolsHtml = arch.sanborn.volumes
      .slice(0, 5)
      .map((vol) => {
        const thumbs = vol.sampleSheets
          .filter((s) => s.localThumbUrl)
          .slice(0, 2)
          .map(
            (s) => `
            <a href="${escHtml(s.externalUrl || vol.locUrl || "#")}" target="_blank" rel="noopener noreferrer" class="audit-sanborn-thumb" title="${escHtml(
              `${vol.title} - ${s.label}`
            )}">
              <img src="${escHtml(s.localThumbUrl)}" alt="${escHtml(s.label)}" loading="lazy" />
              <span>${escHtml(s.label)}</span>
            </a>`
          )
          .join("");
        return `
          <div class="audit-sanborn-vol-card">
            <div class="audit-sanborn-vol-top">
              <div>
                <strong>${escHtml(vol.date || vol.era)}</strong> - ${escHtml(vol.title)}
                <span class="audit-muted-small">(${vol.sheetCount} sheets)</span>
              </div>
              ${
                vol.locUrl
                  ? `<a href="${escHtml(vol.locUrl)}" target="_blank" rel="noopener noreferrer" class="audit-link-chip">Open Volume &#8599;</a>`
                  : ""
              }
            </div>
            ${thumbs ? `<div class="audit-sanborn-thumb-strip">${thumbs}</div>` : ""}
          </div>`;
      })
      .join("");

    archHost.innerHTML = `
      <div class="audit-archival-section">
        <div class="audit-section-header">
          <h4>1866-1926 Houston City Directories (Houston History Research Center)</h4>
        </div>
        ${
          cd.hit
            ? `<div class="audit-ocr-card">
                <div class="audit-ocr-meta">
                  <span>Earliest Hit: <strong>${escHtml(cd.earliestYear)}</strong></span>
                  ${
                    cd.presentYears.length > 0
                      ? `<span>Indexed Volumes: ${escHtml(cd.presentYears.join(", "))}</span>`
                      : ""
                  }
                </div>
                ${
                  cd.ocrExcerpt
                    ? `<blockquote class="audit-ocr-quote">${escHtml(cd.ocrExcerpt)}</blockquote>`
                    : ""
                }
              </div>`
            : cd.isAbsentThrough1926
            ? `<div class="audit-ocr-card absent">
                <strong>Zero OCR matches across 1866-1926 directories.</strong> Verify whether parcel sat outside pre-1926 city limits, stood under a historic street name below, or has a miscoded HCAD build year.
              </div>`
            : `<div class="audit-muted-small">Use the ContentDM openers below to verify exact resident, trade, or street directory entries.</div>`
        }
        ${aliasHtml}
        <div class="audit-chip-row">
          ${
            cd.directPageUrl
              ? `<a href="${escHtml(cd.directPageUrl)}" target="_blank" rel="noopener noreferrer" class="audit-link-chip primary">
                  Open Direct Directory Scan (p.${escHtml(cd.pointer)}) &#8599;
                </a>`
              : ""
          }
          <a href="${escHtml(cd.addressSearchUrl)}" target="_blank" rel="noopener noreferrer" class="audit-link-chip">
            Search "${escHtml(item.address || item.hcad_num)}" in ContentDM &#8599;
          </a>
          <a href="${escHtml(item.source_url)}" target="_blank" rel="noopener noreferrer" class="audit-link-chip">
            Open Primary Finding Source &#8599;
          </a>
        </div>
        ${
          cd.milestoneVolumes.length > 0
            ? `<div class="audit-vol-chips">
                <span class="audit-muted-small">Browse Volume:</span>
                ${cd.milestoneVolumes
                  .map(
                    (v) =>
                      `<a href="${escHtml(v.url)}" target="_blank" rel="noopener noreferrer" class="audit-vol-chip" title="${escHtml(
                        v.title
                      )}">${escHtml(v.label)}</a>`
                  )
                  .join("")}
              </div>`
            : ""
        }
      </div>

      <div class="audit-archival-section">
        <div class="audit-section-header">
          <h4>1877-1955 Sanborn Fire Insurance Atlas Coverage</h4>
          <button type="button" class="audit-link-chip" id="btn-audit-open-sanborn-browser">
            Full Sanborn Sheet Browser
          </button>
        </div>
        <div class="audit-sanborn-boundaries">${sanbornBoundariesHtml}</div>
        <div class="audit-sanborn-vols">${sanbornVolsHtml}</div>
      </div>

      <div class="audit-archival-section">
        <div class="audit-section-header">
          <h4>External Parcel &amp; Street-Level Cross-Checks</h4>
        </div>
        <div class="audit-chip-row">
          <a href="https://search.hcad.org/" target="_blank" rel="noopener noreferrer" class="audit-link-chip" id="btn-audit-copy-hcad">
            Copy ${escHtml(item.hcad_num)} &amp; HCAD Search &#8599;
          </a>
          <a href="${escHtml(hcadGisUrl)}" target="_blank" rel="noopener noreferrer" class="audit-link-chip">
            HCAD GIS Parcel Map &#8599;
          </a>
          <a href="${escHtml(gsvUrl)}" target="_blank" rel="noopener noreferrer" class="audit-link-chip">
            Google Street View &#8599;
          </a>
        </div>
      </div>
    `;

    archHost.querySelector("#btn-audit-open-sanborn-browser")?.addEventListener("click", () => {
      document.getElementById("sanborn-browser-modal")?.classList.remove("hidden");
    });
    archHost.querySelector("#btn-audit-copy-hcad")?.addEventListener("click", () => {
      try {
        navigator?.clipboard?.writeText?.(item.hcad_num);
        this._toast(`Copied HCAD account ${item.hcad_num} to clipboard.`);
      } catch (_err) {
        // Ignore clipboard failure
      }
    });
  }

  // -------------------------------------------------------------------------
  // Component B: In-Browser Staff Polygon Editor (`#5a`)
  // -------------------------------------------------------------------------

  _bindPolygonEditorDomOnce() {
    if (this._polyBound || typeof document === "undefined") return;
    const dock = document.getElementById("polygon-editor-dock");
    if (!dock) return;
    this._polyBound = true;

    document.getElementById("btn-close-polyedit")?.addEventListener("click", () => {
      this.teardownPolyEditSession({ updateUrl: true, restoreBasemap: true });
    });
    document.getElementById("btn-polyedit-cancel")?.addEventListener("click", () => {
      this.teardownPolyEditSession({ updateUrl: true, restoreBasemap: true });
    });
    document.getElementById("btn-polyedit-undo")?.addEventListener("click", () => {
      this.undoPolygonEdit();
    });
    document.getElementById("btn-polyedit-redo")?.addEventListener("click", () => {
      this.redoPolygonEdit();
    });
    document.getElementById("btn-polyedit-delete-vertex")?.addEventListener("click", () => {
      this.deleteActivePolygonVertex();
    });
    document.getElementById("btn-polyedit-new-rect")?.addEventListener("click", () => {
      if (!this.polySession) return;
      const vp = this.mapController?.getCurrentViewport?.() || {
        lng: this.polySession.centerLng,
        lat: this.polySession.centerLat,
      };
      const rect = createRectangleFootprintAroundPoint(vp.lng, vp.lat, {
        widthM: 11,
        depthM: 15,
        angleDeg: 0,
      });
      this._commitPolyRing(extractEditableRing(rect), { traceMode: false });
    });
    document.getElementById("btn-polyedit-trace-mode")?.addEventListener("click", () => {
      if (!this.polySession) return;
      if (!this.polySession.traceMode) {
        this._pushPolyUndo();
        this.polySession.openRing = [];
        this.polySession.traceMode = true;
        this.polySession.activeVertexIndex = -1;
        this._syncPolygonEditorMapAndUi();
        this._toast("Trace Mode active: click roof corners on the satellite map to place vertices.");
      } else {
        this.polySession.traceMode = false;
        this._syncPolygonEditorMapAndUi();
      }
    });

    dock.querySelectorAll("[data-poly-rotate]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const deg = Number(btn.getAttribute("data-poly-rotate") || 0);
        this.applyPolygonRotation(deg);
      });
    });

    const rotSlider = document.getElementById("polyedit-rotate-slider");
    const rotLabel = document.getElementById("polyedit-rotate-value");
    if (rotSlider) {
      rotSlider.addEventListener("input", () => {
        if (!this.polySession || this.polySession.openRing.length < 3) return;
        const deg = Number(rotSlider.value || 0);
        if (rotLabel) rotLabel.textContent = `${deg > 0 ? "+" : ""}${deg.toFixed(1)} deg`;
        if (!this.polySession._sliderBaseRing) {
          this.polySession._sliderBaseRing = this.polySession.openRing.map((p) => [p[0], p[1]]);
          this.polySession._sliderPivot = computePolygonCentroid(this.polySession._sliderBaseRing);
          this._pushPolyUndo(this.polySession._sliderBaseRing);
        }
        const rotated = rotatePolygonTangentPlane(
          this.polySession._sliderBaseRing,
          deg,
          this.polySession._sliderPivot
        );
        this.polySession.openRing = extractEditableRing(rotated);
        this._syncPolygonEditorMapAndUi();
      });
      rotSlider.addEventListener("change", () => {
        if (!this.polySession) return;
        this.polySession._sliderBaseRing = null;
        this.polySession._sliderPivot = null;
        rotSlider.value = "0";
        if (rotLabel) rotLabel.textContent = "0.0 deg";
      });
    }

    dock.querySelectorAll("[data-poly-nudge]").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (!this.polySession || this.polySession.openRing.length < 3) return;
        const dir = btn.getAttribute("data-poly-nudge");
        const step = Number(document.getElementById("polyedit-nudge-step")?.value || 0.5);
        const dx = dir === "E" ? step : dir === "W" ? -step : 0;
        const dy = dir === "N" ? step : dir === "S" ? -step : 0;
        this.applyPolygonNudge(dx, dy);
      });
    });

    document.getElementById("polyedit-select-mode")?.addEventListener("change", (e) => {
      if (!this.polySession) return;
      this.polySession.saveMode = e.target.value || "primary";
      this._updatePolyTargetKeyPreview();
    });
    document.getElementById("polyedit-select-contributing")?.addEventListener("change", () => {
      this._syncPolygonEditorMapAndUi();
    });
    document.getElementById("btn-polyedit-toggle-basemap")?.addEventListener("click", () => {
      if (!this.filterStore) return;
      const cur = this.filterStore.getState().basemap;
      this.filterStore.setState({
        basemap: cur === "satellite" ? "dark_archival" : "satellite",
      });
    });
    document.getElementById("btn-polyedit-save")?.addEventListener("click", () => {
      this.savePolygonOverride();
    });
  }

  async openPolygonEditor(spec = {}) {
    if (typeof document === "undefined") return false;
    this._bindPolygonEditorDomOnce();

    const rawTarget = String(
      spec.targetKey || spec.props?.building_id || spec.hcadNum || spec.props?.hcad_num || ""
    ).trim();
    const cleanHcad = normalizeHcad13Clean(spec.hcadNum || spec.props?.hcad_num || rawTarget);
    if (!cleanHcad && !rawTarget) {
      this._toast("Select a property or HCAD parcel first before opening the Polygon Editor.");
      return false;
    }

    const admin = getAdminSession();
    if (!isAdminUnlockedSession(admin)) {
      this._pendingPolyEditSpec = { ...spec, targetKey: rawTarget || cleanHcad, hcadNum: cleanHcad };
      this._openedAuthGateForPolyEdit = true;
      this.app?.openCorrectionModal?.(spec.props || { hcad_num: cleanHcad }, {
        focusAdminUnlock: true,
      });
      const adminPanel = document.getElementById("admin-auth-panel");
      if (adminPanel) adminPanel.classList.remove("hidden");
      const emailInput = document.getElementById("admin-auth-email");
      setTimeout(() => emailInput?.focus?.(), 60);
      this._toast("Unlock Preservation Houston Staff Admin to trace or reshape footprints.");
      return false;
    }

    if (this.surveyOpen) {
      this.closeFieldSurveySheet({ updateUrl: false });
    }
    this.closeHistorianAuditModal({ updateUrl: false });

    const dock = document.getElementById("polygon-editor-dock");
    if (!dock) return false;
    dock.classList.remove("hidden");

    const curState = this.filterStore?.getState?.() || {};
    if (!this._prePolyBasemap) {
      this._prePolyBasemap = curState.basemap || "dark_archival";
      this._prePolyLayers = { ...(curState.layers || {}) };
    }

    let props = spec.props || this._getInspectedRawProps() || {};
    const preOverrides = this.mapController?.curatedOverrides || {};
    const preOv = preOverrides[rawTarget] || preOverrides[cleanHcad] || null;
    let searchHit = null;
    if (Array.isArray(this.app?.searchIndex) && (rawTarget || cleanHcad)) {
      searchHit =
        this.app.searchIndex.find(
          (it) =>
            !it?.is_neighborhood_entry &&
            rawTarget &&
            (it?.building_id === rawTarget || it?.id === rawTarget)
        ) ||
        this.app.searchIndex.find(
          (it) =>
            !it?.is_neighborhood_entry &&
            cleanHcad &&
            normalizeHcad13Clean(it?.hcad_num) === cleanHcad
        ) ||
        null;
    }
    if (searchHit || preOv) {
      props = {
        ...(searchHit || {}),
        ...(preOv || {}),
        ...Object.fromEntries(
          Object.entries(props || {}).filter(([, v]) => v !== "" && v !== null && v !== undefined)
        ),
      };
    }

    let centerLng = Number(props.lng ?? props.lon);
    let centerLat = Number(props.lat);
    if ((!Number.isFinite(centerLng) || !Number.isFinite(centerLat)) && preOv) {
      const ovRing = extractEditableRing(preOv.geometry || preOv.geometry_geojson);
      if (ovRing.length >= 3) {
        const [oLng, oLat] = computePolygonCentroid(ovRing);
        if (Number.isFinite(oLng) && Number.isFinite(oLat)) {
          centerLng = oLng;
          centerLat = oLat;
        }
      }
    }
    const vp = this.mapController?.getCurrentViewport?.() || { lng: -95.3635, lat: 29.7595, zoom: 18 };
    if (!Number.isFinite(centerLng) || !Number.isFinite(centerLat)) {
      centerLng = vp.lng;
      centerLat = vp.lat;
    }

    // Switch to flat satellite overzoom (`z=19.8`, clamped at native tile `z=18`, `DF-6`)
    this.filterStore?.setState?.({
      basemap: "satellite",
      extrude3D: false,
      activePolyEdit: rawTarget || cleanHcad,
      layers: {
        ...(this.filterStore.getState().layers || {}),
        buildings: true,
        hcadBoundary: true,
      },
    });
    const targetZoom = Math.max(Number(vp.zoom || 0), 19.8);
    if (
      this.mapController?.map &&
      !this.mapController.useCanvasFallback &&
      typeof this.mapController.map.jumpTo === "function"
    ) {
      try {
        this.mapController.map.stop?.();
        this.mapController.map.jumpTo({
          center: [centerLng, centerLat],
          zoom: targetZoom,
          pitch: 0,
        });
      } catch (_err) {
        this.mapController?.flyToLocation?.({
          lng: centerLng,
          lat: centerLat,
          zoom: targetZoom,
          pitch: 0,
        });
      }
    } else {
      this.mapController?.flyToLocation?.({
        lng: centerLng,
        lat: centerLat,
        zoom: targetZoom,
        pitch: 0,
      });
    }

    const statusLine = document.getElementById("polyedit-tile-status");
    if (statusLine) {
      statusLine.textContent = "Checking parcel footprint tiles...";
      statusLine.classList.remove("hidden");
    }

    // Gate on PMTiles shard settlement (`DF-3`) before reading footprint geometry/risk
    if (this.mapController?.awaitParcelShardReadiness) {
      try {
        await this.mapController.awaitParcelShardReadiness(
          cleanHcad,
          [centerLng, centerLat],
          2200
        );
      } catch (_err) {
        // Proceed with fallback geometry
      }
    }
    if (statusLine) statusLine.classList.add("hidden");

    const overrides = this.mapController?.curatedOverrides || {};
    const parcelSummary = this.mapController?.getParcelFootprintSummary?.(cleanHcad, rawTarget) || {
      footprintCount: 0,
      hasMultiPolygon: false,
      subBuildingIds: [],
      matchedGeometry: null,
      matchedProps: null,
    };

    const existingOv = overrides[rawTarget] || overrides[cleanHcad] || null;
    const seedGeom =
      existingOv?.geometry ||
      existingOv?.geometry_geojson ||
      spec.geometry ||
      parcelSummary.matchedGeometry ||
      (this.mapController?.selectedFeatureGeometry?.type?.includes("Polygon")
        ? this.mapController.selectedFeatureGeometry
        : null);

    let seedRing = extractEditableRing(seedGeom);
    let startedFromBlankRect = false;
    if (seedRing.length < 3) {
      const rect = createRectangleFootprintAroundPoint(centerLng, centerLat, {
        widthM: 11,
        depthM: 15,
        angleDeg: 0,
      });
      seedRing = extractEditableRing(rect);
      startedFromBlankRect = true;
    } else {
      const [cLng, cLat] = computePolygonCentroid(seedRing);
      if (Number.isFinite(cLng) && Number.isFinite(cLat)) {
        centerLng = cLng;
        centerLat = cLat;
      }
    }

    const risk = assessMultiFootprintParcelRisk({
      hcadNum: cleanHcad,
      footprintCount: parcelSummary.footprintCount,
      hasMultiPolygon: parcelSummary.hasMultiPolygon,
      subBuildingIds: parcelSummary.subBuildingIds,
      existingOverrides: overrides,
      targetKey: rawTarget || cleanHcad,
    });

    const nextAuxKey = cleanHcad
      ? allocateNextAuxOverrideKey(cleanHcad, overrides, parcelSummary.subBuildingIds)
      : `${rawTarget}#aux_1`;
    const isAlreadyAux = /#aux_\d+$/i.test(rawTarget);
    const isSubShard = rawTarget.includes("#") && !isAlreadyAux;
    const hasExistingBareParcelGeom = Boolean(
      existingOv && (existingOv.geometry || existingOv.geometry_geojson)
    );
    const defaultMode = isAlreadyAux
      ? "additive_aux"
      : isSubShard
      ? "primary"
      : risk.hasMultiFootprintRisk && !hasExistingBareParcelGeom
      ? "additive_aux"
      : "primary";

    const mergedProps = { ...(parcelSummary.matchedProps || {}), ...props };
    const districtName = String(mergedProps.historic_district || "").trim();
    const inCohDistrict =
      Boolean(districtName) &&
      districtName !== "Outside Historic District" &&
      districtName !== "Outside City District";
    const defaultContrib = VALID_CONTRIBUTING_VALUES.has(String(mergedProps.contributing || "").trim())
      ? String(mergedProps.contributing).trim()
      : inCohDistrict
      ? "Contributing"
      : "Outside Historic District";

    this.polySession = {
      hcadNum: cleanHcad,
      requestedKey: rawTarget || cleanHcad,
      nextAuxKey,
      saveMode: defaultMode,
      risk,
      centerLng,
      centerLat,
      openRing: seedRing,
      activeVertexIndex: 0,
      traceMode: false,
      undoStack: [],
      redoStack: [],
      props: mergedProps,
    };

    // Hydrate dock inputs
    const addrEl = document.getElementById("polyedit-input-address");
    const nameEl = document.getElementById("polyedit-input-name");
    const yrEl = document.getElementById("polyedit-input-year");
    const contribEl = document.getElementById("polyedit-select-contributing");
    const citeEl = document.getElementById("polyedit-input-citation");
    const modeEl = document.getElementById("polyedit-select-mode");
    const confirmWrap = document.getElementById("polyedit-replace-confirm-wrap");
    const confirmChk = document.getElementById("polyedit-chk-confirm-replace");

    if (addrEl) addrEl.value = mergedProps.address || "";
    if (nameEl) nameEl.value = mergedProps.building_name || "";
    if (yrEl) {
      const y = Number(mergedProps.year_built);
      yrEl.value = Number.isFinite(y) && y >= MIN_VALID_YEAR ? String(y) : "";
    }
    if (contribEl) contribEl.value = defaultContrib;
    if (citeEl) {
      citeEl.value = stripEmbeddedFieldSurveyTag(
        stripEmbeddedGeometryTag(existingOv?.source_citation || "")
      );
    }
    if (confirmChk) confirmChk.checked = false;
    if (modeEl) {
      const basePrimaryKey = isSubShard ? rawTarget : cleanHcad || rawTarget;
      const additiveKey = isAlreadyAux ? rawTarget : nextAuxKey;
      modeEl.innerHTML = `
        <option value="primary">Reshape Primary Footprint (${escHtml(basePrimaryKey)})</option>
        <option value="additive_aux">Add Secondary Structure (${escHtml(additiveKey)})</option>
        <option value="parcel_replace">Replace All Footprints on Parcel (${escHtml(cleanHcad || basePrimaryKey)})</option>
      `;
      modeEl.value = defaultMode;
    }
    if (confirmWrap) {
      confirmWrap.classList.toggle("hidden", !risk.hasMultiFootprintRisk);
    }

    const warnEl = document.getElementById("polyedit-multifp-warning");
    if (warnEl) {
      if (risk.hasMultiFootprintRisk) {
        warnEl.textContent = risk.warningMessage;
        warnEl.classList.remove("hidden");
      } else {
        warnEl.textContent = "";
        warnEl.classList.add("hidden");
      }
    }

    this._installMapControllerPolyHooks();
    this._updatePolyTargetKeyPreview();
    this._syncPolygonEditorMapAndUi();
    return true;
  }

  _resolveActivePolyOverrideKey() {
    if (!this.polySession) return "";
    const { saveMode, requestedKey, hcadNum, nextAuxKey } = this.polySession;
    if (saveMode === "additive_aux") {
      return /#aux_\d+$/i.test(requestedKey) ? requestedKey : nextAuxKey;
    }
    if (saveMode === "parcel_replace") {
      return hcadNum || requestedKey;
    }
    return requestedKey.includes("#") && !/#aux_\d+$/i.test(requestedKey)
      ? requestedKey
      : hcadNum || requestedKey;
  }

  _updatePolyTargetKeyPreview() {
    const badge = document.getElementById("polyedit-target-badge");
    const confirmWrap = document.getElementById("polyedit-replace-confirm-wrap");
    if (!this.polySession) return;
    const key = this._resolveActivePolyOverrideKey();
    if (badge) badge.textContent = `Target ID: ${key}`;
    if (confirmWrap) {
      const needsConfirm =
        Boolean(this.polySession.risk?.hasMultiFootprintRisk) &&
        (this.polySession.saveMode === "parcel_replace" || !key.includes("#"));
      confirmWrap.classList.toggle("hidden", !needsConfirm);
    }
    this._syncPolygonEditorMapAndUi();
  }

  _installMapControllerPolyHooks() {
    if (!this.mapController?.setPolygonEditorSession) return;
    this.mapController.setPolygonEditorSession({
      getVertices: () => (this.polySession ? this.polySession.openRing : []),
      getMidpoints: () => {
        if (
          !this.polySession ||
          this.polySession.traceMode ||
          this.polySession.openRing.length < 3 ||
          this.polySession.openRing.length >= POLYGON_EDITOR_MAX_VERTICES
        ) {
          return [];
        }
        const r = this.polySession.openRing;
        return r.map((a, i) => {
          const b = r[(i + 1) % r.length];
          return [round6((a[0] + b[0]) * 0.5), round6((a[1] + b[1]) * 0.5)];
        });
      },
      isTraceMode: () => Boolean(this.polySession?.traceMode),
      onSelectVertex: (idx, { altKey = false } = {}) => {
        if (!this.polySession) return;
        if (altKey) {
          this.polySession.activeVertexIndex = idx;
          this.deleteActivePolygonVertex();
          return;
        }
        this.polySession.activeVertexIndex = idx;
        this._syncPolygonEditorMapAndUi();
      },
      onBeginVertexDrag: (idx) => {
        if (!this.polySession) return;
        this._pushPolyUndo();
        this.polySession.activeVertexIndex = idx;
      },
      onDragVertex: (idx, lng, lat) => {
        if (!this.polySession) return;
        const res = updatePolygonVertex(this.polySession.openRing, idx, lng, lat);
        this.polySession.openRing = res.openRing;
        this._syncPolygonEditorMapAndUi();
      },
      onEndVertexDrag: () => {
        this._syncPolygonEditorMapAndUi();
      },
      onInsertMidpoint: (edgeIdx) => {
        if (!this.polySession) return;
        this._pushPolyUndo();
        const res = insertPolygonMidpointVertex(this.polySession.openRing, edgeIdx);
        if (!res.ok) {
          this.polySession.undoStack.pop();
          this._toast(res.error);
          return;
        }
        this.polySession.openRing = res.openRing;
        this.polySession.activeVertexIndex = res.insertedIndex;
        this._syncPolygonEditorMapAndUi();
      },
      onMapTraceClick: (lng, lat) => {
        if (!this.polySession || !this.polySession.traceMode) return;
        if (this.polySession.openRing.length >= POLYGON_EDITOR_MAX_VERTICES) {
          this._toast(`Maximum ${POLYGON_EDITOR_MAX_VERTICES} vertices reached.`);
          return;
        }
        this._pushPolyUndo();
        this.polySession.openRing = [
          ...this.polySession.openRing,
          [round6(lng), round6(lat)],
        ];
        this.polySession.activeVertexIndex = this.polySession.openRing.length - 1;
        this._syncPolygonEditorMapAndUi();
      },
    });
  }

  _pushPolyUndo(snapshotRing = null) {
    if (!this.polySession) return;
    const snap = (snapshotRing || this.polySession.openRing).map((p) => [p[0], p[1]]);
    this.polySession.undoStack.push(snap);
    if (this.polySession.undoStack.length > 60) {
      this.polySession.undoStack.shift();
    }
    this.polySession.redoStack = [];
  }

  _commitPolyRing(nextRing, { traceMode = null } = {}) {
    if (!this.polySession) return;
    this._pushPolyUndo();
    this.polySession.openRing = nextRing.map((p) => [round6(p[0]), round6(p[1])]);
    if (traceMode !== null) this.polySession.traceMode = Boolean(traceMode);
    if (this.polySession.activeVertexIndex >= this.polySession.openRing.length) {
      this.polySession.activeVertexIndex = this.polySession.openRing.length - 1;
    }
    this._syncPolygonEditorMapAndUi();
  }

  undoPolygonEdit() {
    if (!this.polySession || this.polySession.undoStack.length === 0) return;
    this.polySession.redoStack.push(this.polySession.openRing.map((p) => [p[0], p[1]]));
    this.polySession.openRing = this.polySession.undoStack.pop();
    this.polySession.activeVertexIndex = Math.min(
      this.polySession.activeVertexIndex,
      this.polySession.openRing.length - 1
    );
    this._syncPolygonEditorMapAndUi();
  }

  redoPolygonEdit() {
    if (!this.polySession || this.polySession.redoStack.length === 0) return;
    this.polySession.undoStack.push(this.polySession.openRing.map((p) => [p[0], p[1]]));
    this.polySession.openRing = this.polySession.redoStack.pop();
    this.polySession.activeVertexIndex = Math.min(
      this.polySession.activeVertexIndex,
      this.polySession.openRing.length - 1
    );
    this._syncPolygonEditorMapAndUi();
  }

  deleteActivePolygonVertex() {
    if (!this.polySession) return;
    const idx =
      this.polySession.activeVertexIndex >= 0
        ? this.polySession.activeVertexIndex
        : this.polySession.openRing.length - 1;
    if (this.polySession.traceMode && this.polySession.openRing.length <= 3) {
      this._pushPolyUndo();
      this.polySession.openRing = this.polySession.openRing.filter((_p, i) => i !== idx);
      this.polySession.activeVertexIndex = this.polySession.openRing.length - 1;
      this._syncPolygonEditorMapAndUi();
      return;
    }
    const res = removePolygonVertex(this.polySession.openRing, idx);
    if (res.minVerticesReached) {
      this._toast(res.error);
      return;
    }
    this._pushPolyUndo();
    this.polySession.openRing = res.openRing;
    this.polySession.activeVertexIndex = Math.min(idx, res.openRing.length - 1);
    this._syncPolygonEditorMapAndUi();
  }

  applyPolygonRotation(angleDeg) {
    if (!this.polySession || this.polySession.openRing.length < 3) return;
    this._pushPolyUndo();
    const rotated = rotatePolygonTangentPlane(this.polySession.openRing, angleDeg);
    this.polySession.openRing = extractEditableRing(rotated);
    this._syncPolygonEditorMapAndUi();
  }

  applyPolygonNudge(dxMeters, dyMeters) {
    if (!this.polySession || this.polySession.openRing.length < 3) return;
    this._pushPolyUndo();
    const moved = nudgePolygonTangentPlane(this.polySession.openRing, dxMeters, dyMeters);
    this.polySession.openRing = extractEditableRing(moved);
    this._syncPolygonEditorMapAndUi();
  }

  _syncPolygonEditorMapAndUi() {
    if (!this.polySession) return;
    const validation = validateAndNormalizeDraftPolygon(this.polySession.openRing);
    this.polySession.lastValidation = validation;
    const fc = buildPolygonEditorGeoJson(this.polySession.openRing, {
      activeVertexIndex: this.polySession.activeVertexIndex,
      isValid: validation.ok,
      traceClosed: !this.polySession.traceMode || this.polySession.openRing.length >= 3,
    });
    this.mapController?.updatePolygonEditorData?.(fc);

    const sqftEl = document.getElementById("polyedit-stat-sqft");
    const vertEl = document.getElementById("polyedit-stat-vertices");
    const validBadge = document.getElementById("polyedit-validation-badge");
    const btnUndo = document.getElementById("btn-polyedit-undo");
    const btnRedo = document.getElementById("btn-polyedit-redo");
    const btnTrace = document.getElementById("btn-polyedit-trace-mode");
    const btnSave = document.getElementById("btn-polyedit-save");
    const contribVal = document.getElementById("polyedit-select-contributing")?.value || "";
    const contribCheck = validatePolygonSaveContributing(contribVal);

    const areaSqFt = computePolygonAreaSqFt(this.polySession.openRing);
    if (sqftEl) {
      sqftEl.textContent = `${Math.round(areaSqFt).toLocaleString()} sq ft`;
    }
    if (vertEl) {
      vertEl.textContent = `${this.polySession.openRing.length} / ${POLYGON_EDITOR_MAX_VERTICES} pts`;
    }
    if (btnUndo) btnUndo.disabled = this.polySession.undoStack.length === 0;
    if (btnRedo) btnRedo.disabled = this.polySession.redoStack.length === 0;
    if (btnTrace) {
      btnTrace.classList.toggle("active", Boolean(this.polySession.traceMode));
      btnTrace.textContent = this.polySession.traceMode
        ? "Finish Trace"
        : "Trace Corners";
    }

    if (validBadge) {
      if (validation.ok && contribCheck.ok) {
        validBadge.textContent = "Valid Footprint (CCW, 6dp)";
        validBadge.className = "polyedit-valid-badge ok";
      } else {
        validBadge.textContent = validation.error || contribCheck.error;
        validBadge.className = "polyedit-valid-badge err";
      }
    }
    if (btnSave) {
      btnSave.disabled = !validation.ok || !contribCheck.ok;
    }
  }

  async savePolygonOverride() {
    if (!this.polySession) return;
    const admin = getAdminSession();
    if (!isAdminUnlockedSession(admin)) {
      this._toast("Staff Admin session expired; unlock Admin first.");
      return;
    }
    const validation = validateAndNormalizeDraftPolygon(this.polySession.openRing);
    if (!validation.ok) {
      this._toast(validation.error);
      return;
    }
    const contribVal = document.getElementById("polyedit-select-contributing")?.value || "";
    const contribCheck = validatePolygonSaveContributing(contribVal);
    if (!contribCheck.ok) {
      this._toast(contribCheck.error);
      document.getElementById("polyedit-select-contributing")?.focus?.();
      return;
    }

    const overrideKey = this._resolveActivePolyOverrideKey();
    const replacesWholeParcel =
      this.polySession.saveMode === "parcel_replace" || !overrideKey.includes("#");
    if (
      this.polySession.risk?.hasMultiFootprintRisk &&
      replacesWholeParcel &&
      !document.getElementById("polyedit-chk-confirm-replace")?.checked
    ) {
      this._toast("Confirm multi-footprint parcel replacement checkbox first, or choose Add Secondary Structure.");
      return;
    }

    const rawYr = String(document.getElementById("polyedit-input-year")?.value || "").trim();
    const yrNum = rawYr ? Number(rawYr) : null;
    if (
      rawYr &&
      (!Number.isInteger(yrNum) || yrNum < MIN_VALID_YEAR || yrNum > MAX_STANDING_OVERRIDE_YEAR)
    ) {
      this._toast(`Year Built must be blank or between ${MIN_VALID_YEAR} and ${MAX_STANDING_OVERRIDE_YEAR}.`);
      return;
    }

    const address = String(
      document.getElementById("polyedit-input-address")?.value || this.polySession.props?.address || ""
    ).trim();
    const buildingName = String(
      document.getElementById("polyedit-input-name")?.value || ""
    ).trim();
    const userCitation = stripEmbeddedFieldSurveyTag(
      stripEmbeddedGeometryTag(document.getElementById("polyedit-input-citation")?.value || "")
    );
    const citation =
      userCitation ||
      `Staff satellite footprint curation (${Math.round(validation.areaSqFt).toLocaleString()} sq ft, ${validation.vertexCount} vertices)`;
    const replaceParcelShards = this.polySession.saveMode === "parcel_replace";

    const btnSave = document.getElementById("btn-polyedit-save");
    if (btnSave) {
      btnSave.disabled = true;
      btnSave.textContent = "Saving Footprint...";
    }

    const existingOv = this.mapController?.curatedOverrides?.[overrideKey] || null;
    const priorSourceType = String(existingOv?.source_type || "").trim();
    const effectiveSourceType = priorSourceType || "Preservation Houston Staff Polygon Editor";
    const priorYearBuilt = Number(existingOv?.year_built ?? this.polySession.props?.year_built) || "";
    const districtName = String(
      existingOv?.historic_district || this.polySession.props?.historic_district || ""
    ).trim();

    const payload = {
      hcad_num: overrideKey,
      address,
      building_name: buildingName,
      historic_district:
        districtName &&
        districtName !== "Outside Historic District" &&
        districtName !== "Outside City District"
          ? districtName
          : "",
      current_year_built: priorYearBuilt,
      suggested_year_built: yrNum || "",
      contributing: contribCheck.value,
      source_type: effectiveSourceType,
      source_citation: citation,
      source_url: existingOv?.source_url || "",
      footprint_issue:
        this.polySession.saveMode === "additive_aux"
          ? `Added secondary building footprint (${overrideKey})`
          : `Reshaped building footprint (${overrideKey})`,
      geometry_geojson: JSON.stringify(validation.geometry),
      replace_parcel_shards: replaceParcelShards,
    };

    const webhookUrl = this._getWebhookUrl();
    let res = null;
    let remoteError = "";
    try {
      res = await submitAdminApprovedOverride(payload, webhookUrl);
    } catch (err) {
      remoteError = err?.message || String(err);
      const isOfflineErr =
        (typeof navigator !== "undefined" && navigator.onLine === false) ||
        err?.name === "TypeError" ||
        /failed to fetch|networkerror|load failed|network request failed/i.test(remoteError);
      if (!isOfflineErr) {
        if (btnSave) {
          btnSave.disabled = false;
          btnSave.textContent = "Save Approved Footprint";
        }
        const validBadge = document.getElementById("polyedit-validation-badge");
        if (validBadge) {
          validBadge.textContent = `Publish failed: ${remoteError}`;
          validBadge.className = "polyedit-valid-badge err";
        }
        this._toast(`Footprint publish rejected: ${remoteError}`);
        return;
      }
      try {
        res = await submitAdminApprovedOverride(payload, "");
      } catch (_localErr) {
        res = null;
      }
    }

    const isSubOverride = overrideKey.includes("#");
    const livePatch = res?.override || {
      hcad_num: normalizeHcad13Clean(overrideKey),
      address: address || undefined,
      building_name: buildingName || undefined,
      year_built: yrNum || undefined,
      decade: yrNum ? Math.floor(yrNum / 10) * 10 : undefined,
      contributing: contribCheck.value,
      source_type: effectiveSourceType,
      source_citation: citation,
      geometry: validation.geometry,
      is_building_override: isSubOverride,
      suppress_only: false,
      replace_parcel_shards: isSubOverride ? false : replaceParcelShards,
      keep_shard_footprints: false,
    };
    const [cLng, cLat] = computePolygonCentroid(validation.geometry);
    this.app?.applyLiveCuratedOverrideMapUpdate?.(overrideKey, livePatch, validation.geometry);

    if (btnSave) {
      btnSave.disabled = false;
      btnSave.textContent = "Save Approved Footprint";
    }

    this.teardownPolyEditSession({ updateUrl: true, restoreBasemap: false });
    this.mapController?.selectFeatureByIdOrHcad?.({
      hcadNum: normalizeHcad13Clean(overrideKey),
      featureId: overrideKey,
      addressQuery: address,
      targetLng: cLng,
      targetLat: cLat,
      flyTo: false,
    });
    this._toast(
      res?.webhookDelivered
        ? `Saved & synced footprint ${overrideKey} (${Math.round(validation.areaSqFt).toLocaleString()} sq ft).`
        : `Applied footprint ${overrideKey} live on map (${
            remoteError || "webhook offline / export if needed"
          }).`
    );
  }

  /**
   * Strictly idempotent Polygon Editor teardown (`RP-1`).
   * Safe to invoke multiple times in succession on Escape, Cancel, Save, or Lock.
   */
  teardownPolyEditSession({ updateUrl = true, restoreBasemap = true } = {}) {
    this._pendingPolyEditSpec = null;
    this.polySession = null;
    if (this.mapController?.setPolygonEditorSession) {
      this.mapController.setPolygonEditorSession(null);
    }
    if (typeof document !== "undefined") {
      document.getElementById("polygon-editor-dock")?.classList.add("hidden");
    }
    if (this.filterStore) {
      const nextPatch = {};
      if (updateUrl && this.filterStore.getState().activePolyEdit) {
        nextPatch.activePolyEdit = null;
      }
      if (restoreBasemap && this._prePolyBasemap) {
        nextPatch.basemap = this._prePolyBasemap;
        if (this._prePolyLayers) {
          nextPatch.layers = { ...this._prePolyLayers };
        }
      }
      if (Object.keys(nextPatch).length > 0) {
        this.filterStore.setState(nextPatch);
      }
    }
    this._prePolyBasemap = null;
    this._prePolyLayers = null;
  }

  // -------------------------------------------------------------------------
  // Component C: Mobile "Walk-the-Block" Field Survey Mode (`#5b`)
  // -------------------------------------------------------------------------

  _bindFieldSurveyDomOnce() {
    if (this._surveyBound || typeof document === "undefined") return;
    const sheet = document.getElementById("field-survey-sheet");
    if (!sheet) return;
    this._surveyBound = true;

    document.getElementById("btn-close-field-survey")?.addEventListener("click", () => {
      this.closeFieldSurveySheet({ updateUrl: true });
    });

    sheet.querySelectorAll("[data-survey-snap]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const snap = btn.getAttribute("data-survey-snap") || "half";
        this.setFieldSurveySnap(snap);
      });
    });

    // Touch drag gesture on grab handle (`peek` <-> `half` <-> `full`)
    const handle = document.getElementById("field-survey-drag-handle");
    if (handle) {
      let startY = null;
      let startSnap = "half";
      handle.addEventListener(
        "touchstart",
        (e) => {
          if (!e.touches || e.touches.length !== 1) return;
          startY = e.touches[0].clientY;
          startSnap = this.surveySnap;
        },
        { passive: true }
      );
      handle.addEventListener(
        "touchend",
        (e) => {
          if (startY === null) return;
          const endY = e.changedTouches?.[0]?.clientY ?? startY;
          const dy = endY - startY;
          startY = null;
          if (Math.abs(dy) < 28) {
            // Simple tap cycles half <-> full <-> peek ergonomically
            this.setFieldSurveySnap(
              startSnap === "peek" ? "half" : startSnap === "half" ? "full" : "half"
            );
            return;
          }
          if (dy < -30) {
            this.setFieldSurveySnap(startSnap === "peek" ? "half" : "full");
          } else if (dy > 30) {
            this.setFieldSurveySnap(startSnap === "full" ? "half" : "peek");
          }
        },
        { passive: true }
      );
    }

    document.getElementById("btn-survey-daylight-toggle")?.addEventListener("click", () => {
      this.setFieldSurveyDaylight(!this.surveyDaylight);
    });

    document.getElementById("btn-survey-locate")?.addEventListener("click", () => {
      if (this.surveyGpsEnabled) {
        this._stopSurveyGpsWatch({ manualOff: true });
      } else {
        this._startSurveyGpsWatch({ centerOnFirstFix: true });
      }
    });

    document.getElementById("btn-survey-pick-center")?.addEventListener("click", () => {
      this.selectSurveyHouseAtMapCenter();
    });

    document.getElementById("btn-survey-refresh-nearby")?.addEventListener("click", () => {
      this.refreshSurveyCandidates({ forceUnlock: true, selectNearestIfEmpty: false });
    });

    const surveyorInput = document.getElementById("survey-input-surveyor");
    if (surveyorInput) {
      surveyorInput.addEventListener("input", () => {
        this.surveyDraft.surveyor_name = surveyorInput.value;
        this._saveSurveyPrefs({ surveyor_name: surveyorInput.value.trim() });
      });
    }
    const distInput = document.getElementById("survey-input-district");
    if (distInput) {
      distInput.addEventListener("input", () => {
        this.surveyDraft.district = distInput.value;
        this._saveSurveyPrefs({ district: distInput.value.trim() });
      });
    }
    const manualAddr = document.getElementById("survey-input-manual-address");
    if (manualAddr) {
      manualAddr.addEventListener("input", () => {
        this.surveyDraft.address = manualAddr.value;
        this.surveyCardDirty = true;
        this._updateSurveyActiveHeader();
      });
    }

    // Populate Integrity tiles, Condition pills, Contributing pills, and select options once
    const integrityGrid = document.getElementById("survey-integrity-grid");
    if (integrityGrid) {
      const shortLabels = {
        Intact: { title: "Intact", sub: "Original form & detail" },
        "Minor Alterations": {
          title: "Minor Alt.",
          sub: "Reversible window/siding",
        },
        "Substantially Altered": { title: "Substantially Alt.", sub: "Major form/material loss" },
        "Vacant / Boarded": { title: "Vacant / Boarded", sub: "Unoccupied or boarded" },
        "Demolished / Vacant Lot": { title: "Demolished / Lot", sub: "Cleared or replaced" },
        "Storm / Hazard Damaged": { title: "Storm / Hazard", sub: "Structural emergency" },
      };
      integrityGrid.innerHTML = FIELD_SURVEY_INTEGRITY_GRADES.map((g) => {
        const meta = shortLabels[g] || { title: g, sub: "" };
        return `<button type="button" class="survey-integrity-tile" data-integrity-grade="${escHtml(
          g
        )}" aria-pressed="false">
          <span class="survey-tile-check" aria-hidden="true">&#10003;</span>
          <span class="survey-tile-title">${escHtml(meta.title)}</span>
          <span class="survey-tile-sub">${escHtml(meta.sub)}</span>
        </button>`;
      }).join("");
      integrityGrid.querySelectorAll("[data-integrity-grade]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const grade = btn.getAttribute("data-integrity-grade") || "";
          this._selectSurveyIntegrityGrade(grade);
        });
      });
    }

    const condRow = document.getElementById("survey-condition-pills");
    if (condRow) {
      condRow.innerHTML = FIELD_SURVEY_CONDITION_RATINGS.map(
        (c) =>
          `<button type="button" class="survey-choice-pill" data-condition-rating="${escHtml(
            c
          )}" aria-pressed="false">${escHtml(c)}</button>`
      ).join("");
      condRow.querySelectorAll("[data-condition-rating]").forEach((btn) => {
        btn.addEventListener("click", () => {
          this.surveyDraft.condition_rating = btn.getAttribute("data-condition-rating") || "Good";
          this.surveyManualCondOverride = true;
          this.surveyCardDirty = true;
          this._syncSurveyFormControls();
        });
      });
    }

    const contribRow = document.getElementById("survey-contributing-pills");
    if (contribRow) {
      contribRow.innerHTML = SURVEY_CONTRIBUTING_VALUES.map(
        (c) =>
          `<button type="button" class="survey-choice-pill" data-contrib-status="${escHtml(
            c
          )}" aria-pressed="false">${escHtml(c)}</button>`
      ).join("");
      contribRow.querySelectorAll("[data-contrib-status]").forEach((btn) => {
        btn.addEventListener("click", () => {
          this.surveyDraft.contributing_status =
            btn.getAttribute("data-contrib-status") || "Contributing";
          this.surveyManualContribOverride = true;
          this.surveyCardDirty = true;
          this._syncSurveyFormControls();
        });
      });
    }

    const fillSelect = (id, placeholder, values) => {
      const sel = document.getElementById(id);
      if (!sel) return;
      sel.innerHTML =
        `<option value="">${escHtml(placeholder)}</option>` +
        values.map((v) => `<option value="${escHtml(v)}">${escHtml(v)}</option>`).join("");
    };
    fillSelect("survey-select-windows", "Windows (optional)", SURVEY_WINDOW_MATERIALS);
    fillSelect("survey-select-cladding", "Cladding / Wall (optional)", SURVEY_CLADDING_TYPES);
    fillSelect("survey-select-porch", "Porch State (optional)", SURVEY_PORCH_STATES);

    const bindField = (id, key) => {
      const el = document.getElementById(id);
      if (!el) return;
      const evt = el.tagName === "SELECT" ? "change" : "input";
      el.addEventListener(evt, () => {
        this.surveyDraft[key] = el.value;
        this.surveyCardDirty = true;
        this._updateSurveyDetailSummaryText();
      });
    };
    bindField("survey-select-windows", "window_material");
    bindField("survey-select-cladding", "cladding_material");
    bindField("survey-select-porch", "porch_alteration");
    bindField("survey-input-observed-year", "observed_year");
    bindField("survey-input-photo-url", "photo_url");
    bindField("survey-input-notes", "field_notes");

    // Soft-keyboard ergonomic scroll + auto-expand to `full` snap when typing
    sheet.querySelectorAll("input, textarea, select").forEach((ctrl) => {
      ctrl.addEventListener("focus", () => {
        if (ctrl.type === "file") return;
        if (this.surveySnap === "peek") {
          this.setFieldSurveySnap("half");
        }
        if (
          (ctrl.tagName === "TEXTAREA" || ctrl.id === "survey-input-notes") &&
          this.surveySnap !== "full"
        ) {
          this.setFieldSurveySnap("full");
        }
        setTimeout(() => {
          try {
            ctrl.scrollIntoView({ block: "nearest", behavior: "smooth" });
          } catch (_err) {
            // Ignore scroll error
          }
        }, 120);
      });
    });

    // Phone Camera capture -> canvas JPEG compress -> IndexedDB
    const photoBtn = document.getElementById("btn-survey-take-photo");
    const photoInput = document.getElementById("survey-photo-input");
    if (photoBtn && photoInput) {
      photoBtn.addEventListener("click", () => photoInput.click());
      photoInput.addEventListener("change", async (e) => {
        const file = e.target?.files?.[0];
        if (!file) return;
        await this._handleSurveyPhotoFile(file);
        photoInput.value = "";
      });
    }
    document.getElementById("btn-survey-clear-photo")?.addEventListener("click", async () => {
      await this._disposeUnsavedSurveyDraftPhoto();
      this._renderSurveyPhotoPreview();
      this._refreshSurveyPhotoCounter();
    });

    document.getElementById("btn-survey-save-next")?.addEventListener("click", () => {
      this.saveCurrentSurveyObservation({ advanceNext: true });
    });
    document.getElementById("btn-survey-save-stay")?.addEventListener("click", () => {
      this.saveCurrentSurveyObservation({ advanceNext: false });
    });

    document.getElementById("btn-survey-sync-badge")?.addEventListener("click", () => {
      const qDrawer = document.getElementById("survey-queue-drawer");
      if (qDrawer) {
        qDrawer.open = !qDrawer.open;
        if (qDrawer.open && this.surveySnap === "peek") {
          this.setFieldSurveySnap("half");
        }
      }
    });
    document.getElementById("btn-survey-sync-now")?.addEventListener("click", () => {
      this.syncOfflineSurveyQueueNow();
    });
    document.getElementById("btn-survey-export-csv")?.addEventListener("click", () => {
      this.downloadSurveyQueueCsv();
    });
    document.getElementById("btn-survey-share-photos")?.addEventListener("click", () => {
      this.shareOrDownloadQueuedSurveyPhotos();
    });
    document.getElementById("btn-survey-clear-synced")?.addEventListener("click", () => {
      const remaining = clearSyncedFieldSurveys();
      this._refreshSurveyQueueUi();
      this._toast(`Cleared synced entries (${remaining.remainingCount} unsynced observations kept).`);
    });

    document.getElementById("btn-survey-toast-undo")?.addEventListener("click", () => {
      this.undoLastSurveySave();
    });
  }

  openFieldSurveySheet({ targetProps = null, updateUrl = true } = {}) {
    if (typeof document === "undefined") return;
    this._bindFieldSurveyDomOnce();

    if (this.polySession) {
      this.teardownPolyEditSession({ updateUrl: false, restoreBasemap: false });
    }
    this.closeHistorianAuditModal({ updateUrl: false });

    const sheet = document.getElementById("field-survey-sheet");
    const crosshair = document.getElementById("field-survey-crosshair");
    if (!sheet) return;

    this.surveyOpen = true;
    document.body.classList.add("field-survey-active");
    sheet.classList.remove("hidden");
    crosshair?.classList.remove("hidden");
    document.getElementById("inspector-drawer")?.classList.add("hidden");

    const curState = this.filterStore?.getState?.() || {};
    if (!this._preSurveyBasemap) {
      this._preSurveyBasemap = curState.basemap || "dark_archival";
    }

    const prefs = this._loadSurveyPrefs();
    const isMobileScreen =
      typeof window !== "undefined" && Number(window.innerWidth || 1024) <= 900;
    const wantDaylight =
      typeof prefs.daylight === "boolean" ? prefs.daylight : isMobileScreen;
    this.setFieldSurveyDaylight(wantDaylight, { persist: false });
    this.setFieldSurveySnap("half");

    // Flat 2D top-down orientation at walking zoom (`>= 18.4`)
    const vp = this.mapController?.getCurrentViewport?.() || {
      lng: -95.3635,
      lat: 29.7595,
      zoom: 16,
    };
    const nextPatch = { extrude3D: false };
    if (updateUrl) nextPatch.activeSurvey = true;
    this.filterStore?.setState?.(nextPatch);

    if (Number(vp.zoom || 0) < 18.2 || Number(vp.pitch || 0) > 2) {
      this.mapController?.flyToLocation?.({
        lng: Number(targetProps?.lng ?? targetProps?.lon ?? vp.lng),
        lat: Number(targetProps?.lat ?? vp.lat),
        zoom: Math.max(Number(vp.zoom || 0), 18.6),
        pitch: 0,
      });
    }

    // Intercept upper-map taps so tapping any house roof loads it straight into the sheet
    this.mapController?.setInteractiveMapTapInterceptor?.((tapPayload) => {
      if (!this.surveyOpen) return false;
      this._handleSurveyMapTap(tapPayload);
      return true;
    });

    this._installSurveyViewportAndVisibilityListeners();

    const surveyorEl = document.getElementById("survey-input-surveyor");
    const distEl = document.getElementById("survey-input-district");
    this.surveyDraft = this._createBlankSurveyDraft();
    if (surveyorEl) surveyorEl.value = this.surveyDraft.surveyor_name;

    // Auto-fill district from geographic context if blank
    if (!this.surveyDraft.district && this.mapController?.resolveGeographicContextAtPoint) {
      const ctx = this.mapController.resolveGeographicContextAtPoint(vp.lng, vp.lat);
      const autoDist =
        (ctx?.historicDistrict &&
          ctx.historicDistrict !== "Outside City District" &&
          ctx.historicDistrict) ||
        ctx?.potentialHistoricDistrict ||
        ctx?.neighborhood ||
        "";
      if (autoDist) this.surveyDraft.district = autoDist;
    }
    if (distEl) distEl.value = this.surveyDraft.district;

    this.surveySelectionLocked = false;
    this.refreshSurveyCandidates({ forceUnlock: true, selectNearestIfEmpty: !targetProps });
    if (targetProps && (targetProps.hcad_num || targetProps.address || targetProps.building_id)) {
      this._loadCandidateIntoSurveyCard(this._candidateFromRawProps(targetProps), {
        panMap: false,
        lockSelection: true,
      });
    }
    this._refreshSurveyQueueUi();
    this._refreshSurveyPhotoCounter();
  }

  closeFieldSurveySheet({ updateUrl = true } = {}) {
    this.surveyOpen = false;
    void this._disposeUnsavedSurveyDraftPhoto();
    this._flushDeferredSurveySyncTimers();
    this._stopSurveyGpsWatch({ manualOff: true });
    this._removeSurveyViewportAndVisibilityListeners();
    this.mapController?.setInteractiveMapTapInterceptor?.(null);
    this.mapController?.setFieldSurveyOverlay?.(null);

    if (typeof document !== "undefined") {
      document.body.classList.remove("field-survey-active", "field-survey-daylight");
      const sheet = document.getElementById("field-survey-sheet");
      if (sheet) {
        sheet.classList.add("hidden");
        sheet.style.removeProperty("--vv-keyboard-offset");
      }
      document.getElementById("field-survey-crosshair")?.classList.add("hidden");
      document.getElementById("field-survey-toast")?.classList.add("hidden");
    }

    if (this.filterStore) {
      const patch = {};
      if (updateUrl && this.filterStore.getState().activeSurvey) {
        patch.activeSurvey = false;
      }
      if (this._preSurveyBasemap) {
        patch.basemap = this._preSurveyBasemap;
      }
      if (Object.keys(patch).length > 0) {
        this.filterStore.setState(patch);
      }
    }
    this._preSurveyBasemap = null;
  }

  setFieldSurveySnap(snap) {
    const valid = snap === "peek" || snap === "full" ? snap : "half";
    this.surveySnap = valid;
    if (typeof document === "undefined") return;
    const sheet = document.getElementById("field-survey-sheet");
    if (sheet) sheet.setAttribute("data-snap", valid);
    document.body.setAttribute("data-survey-snap", valid);
    sheet?.querySelectorAll("[data-survey-snap]").forEach((btn) => {
      const isAct = btn.getAttribute("data-survey-snap") === valid;
      btn.classList.toggle("active", isAct);
      btn.setAttribute("aria-pressed", isAct ? "true" : "false");
    });
  }

  setFieldSurveyDaylight(enabled, { persist = true } = {}) {
    this.surveyDaylight = Boolean(enabled);
    if (typeof document !== "undefined") {
      document.body.classList.toggle("field-survey-daylight", this.surveyDaylight);
      const btn = document.getElementById("btn-survey-daylight-toggle");
      if (btn) {
        btn.classList.toggle("active", this.surveyDaylight);
        btn.setAttribute("aria-pressed", this.surveyDaylight ? "true" : "false");
        btn.textContent = this.surveyDaylight ? "Daylight: ON" : "Daylight: OFF";
      }
    }
    if (persist) {
      this._saveSurveyPrefs({ daylight: this.surveyDaylight });
    }
    if (this.filterStore && this.surveyOpen) {
      const curBase = this.filterStore.getState().basemap;
      if (this.surveyDaylight && curBase === "dark_archival") {
        this.filterStore.setState({ basemap: "warm_parchment" });
      } else if (
        !this.surveyDaylight &&
        curBase === "warm_parchment" &&
        this._preSurveyBasemap &&
        this._preSurveyBasemap !== "warm_parchment"
      ) {
        this.filterStore.setState({ basemap: this._preSurveyBasemap });
      }
    }
  }

  _installSurveyViewportAndVisibilityListeners() {
    if (typeof window === "undefined") return;
    this._removeSurveyViewportAndVisibilityListeners();

    if (window.visualViewport) {
      this._vvHandler = () => {
        if (!this.surveyOpen) return;
        const vv = window.visualViewport;
        const sheet = document.getElementById("field-survey-sheet");
        if (!vv || !sheet) return;
        const rawOffset = Math.max(0, window.innerHeight - vv.height - vv.offsetTop);
        sheet.style.setProperty("--vv-keyboard-offset", `${Math.round(rawOffset)}px`);
        sheet.classList.toggle("keyboard-open", rawOffset > 90);
      };
      window.visualViewport.addEventListener("resize", this._vvHandler);
      window.visualViewport.addEventListener("scroll", this._vvHandler);
    }

    if (typeof document !== "undefined") {
      this._visibilityHandler = () => {
        if (!this.surveyOpen || !this.surveyGpsEnabled) return;
        if (document.hidden) {
          this._clearNativeGeoWatchOnly();
        } else {
          this._startSurveyGpsWatch({ centerOnFirstFix: false });
        }
      };
      document.addEventListener("visibilitychange", this._visibilityHandler);
    }
  }

  _removeSurveyViewportAndVisibilityListeners() {
    if (typeof window !== "undefined" && window.visualViewport && this._vvHandler) {
      window.visualViewport.removeEventListener("resize", this._vvHandler);
      window.visualViewport.removeEventListener("scroll", this._vvHandler);
    }
    this._vvHandler = null;
    if (typeof document !== "undefined" && this._visibilityHandler) {
      document.removeEventListener("visibilitychange", this._visibilityHandler);
    }
    this._visibilityHandler = null;
  }

  _clearNativeGeoWatchOnly() {
    if (
      this.surveyGpsWatchId !== null &&
      typeof navigator !== "undefined" &&
      navigator.geolocation?.clearWatch
    ) {
      try {
        navigator.geolocation.clearWatch(this.surveyGpsWatchId);
      } catch (_err) {
        // Ignore clearWatch errors
      }
    }
    this.surveyGpsWatchId = null;
  }

  _stopSurveyGpsWatch({ manualOff = true } = {}) {
    this._clearNativeGeoWatchOnly();
    if (manualOff) {
      this.surveyGpsEnabled = false;
      this.surveyGpsFix = null;
      const btn = document.getElementById("btn-survey-locate");
      if (btn) {
        btn.classList.remove("active");
        btn.setAttribute("aria-pressed", "false");
        btn.textContent = "Locate Me";
      }
      this._renderSurveyGpsBanner(null, "");
      this._syncSurveyMapOverlay();
    }
  }

  _startSurveyGpsWatch({ centerOnFirstFix = true } = {}) {
    const btn = document.getElementById("btn-survey-locate");
    if (typeof navigator === "undefined" || !navigator.geolocation) {
      this.surveyGpsEnabled = false;
      this._renderSurveyGpsBanner(
        null,
        "Browser GPS unavailable on this device. Pan the map under the reticle or tap any roof."
      );
      this.refreshSurveyCandidates({ forceUnlock: true, selectNearestIfEmpty: false });
      return;
    }
    this._clearNativeGeoWatchOnly();
    this.surveyGpsEnabled = true;
    if (btn) {
      btn.classList.add("active");
      btn.setAttribute("aria-pressed", "true");
      btn.textContent = "GPS On";
    }
    let handledFirst = !centerOnFirstFix;
    this.surveyGpsWatchId = navigator.geolocation.watchPosition(
      (pos) => {
        if (!this.surveyOpen) return;
        const lng = Number(pos?.coords?.longitude);
        const lat = Number(pos?.coords?.latitude);
        const rawAcc = pos?.coords?.accuracy;
        const accuracy = rawAcc === null || rawAcc === undefined || rawAcc === "" ? null : Number(rawAcc);
        if (!Number.isFinite(lng) || !Number.isFinite(lat)) return;

        if (this.surveyLastStepFix) {
          const stepM = haversineDistanceMeters(
            this.surveyLastStepFix.lng,
            this.surveyLastStepFix.lat,
            lng,
            lat
          );
          if (stepM >= 8) {
            this.surveyWalkingBearingDeg = compassBearingDegrees(
              this.surveyLastStepFix.lng,
              this.surveyLastStepFix.lat,
              lng,
              lat
            );
            this.surveyLastStepFix = { lng, lat };
          }
        } else {
          this.surveyLastStepFix = { lng, lat };
        }

        this.surveyGpsFix = { lng, lat, accuracy, timestamp: Date.now() };
        const accEval = evaluateGpsAccuracy(accuracy);
        this._renderSurveyGpsBanner(accEval, accEval.warningMessage);

        if (!handledFirst) {
          handledFirst = true;
          // On initial Locate Me fix, clear any stale initial viewport auto-selection unless edited/locked
          if (!this.surveyCardDirty && !this.surveySelectionLocked) {
            this.surveyActiveCandidate = null;
            this.surveyDraft.hcad_num = "";
            this.surveyDraft.building_id = "";
            this.surveyDraft.address = "";
            this._updateSurveyActiveHeader();
          }
          this.mapController?.flyToLocation?.({
            lng,
            lat,
            zoom: Math.max(Number(this.mapController?.getCurrentViewport?.()?.zoom || 0), 18.8),
            pitch: 0,
          });
          setTimeout(() => {
            if (this.surveyOpen) {
              this.refreshSurveyCandidates({
                forceUnlock: !this.surveyCardDirty && !this.surveySelectionLocked,
                selectNearestIfEmpty: Boolean(accEval.allowAutoSnap),
              });
            }
          }, 260);
          return;
        }

        // Anti-Jitter Lock: update GPS halo + live distances without scrambling manual or dirty card edits!
        this._syncSurveyMapOverlay();
        if (!this.surveyActiveCandidate && !this.surveyCardDirty && !this.surveySelectionLocked) {
          this.refreshSurveyCandidates({
            forceUnlock: true,
            selectNearestIfEmpty: Boolean(accEval.allowAutoSnap),
          });
        }
      },
      (err) => {
        this._clearNativeGeoWatchOnly();
        this.surveyGpsEnabled = false;
        if (btn) {
          btn.classList.remove("active");
          btn.setAttribute("aria-pressed", "false");
          btn.textContent = "Locate Me";
        }
        const reason =
          err?.code === 1
            ? "Location permission denied. Pan the map or tap any house roof directly to survey."
            : "GPS signal unavailable. Pan the map or tap any house roof directly to survey.";
        this._renderSurveyGpsBanner(null, reason);
        this.refreshSurveyCandidates({ forceUnlock: true, selectNearestIfEmpty: false });
      },
      {
        enableHighAccuracy: true,
        maximumAge: 4000,
        timeout: 12000,
      }
    );
  }

  _renderSurveyGpsBanner(accEval, message) {
    const banner = document.getElementById("survey-gps-banner");
    const gpsPill = document.getElementById("survey-gps-status-pill");
    if (gpsPill) {
      gpsPill.textContent = accEval ? accEval.label : "Map Aim Mode";
      gpsPill.className = `survey-gps-pill mono ${accEval?.badgeTone || "neutral"}`;
    }
    if (!banner) return;
    if (message) {
      banner.textContent = message;
      banner.classList.remove("hidden");
    } else {
      banner.textContent = "";
      banner.classList.add("hidden");
    }
  }

  _getSurveyedKeysSet() {
    const keys = new Set();
    for (const r of getLocalFieldSurveys()) {
      if (r.building_id) keys.add(String(r.building_id));
      if (r.hcad_num) keys.add(String(r.hcad_num));
      if (r.address) keys.add(normalizeComparableAddress(r.address));
    }
    return keys;
  }

  refreshSurveyCandidates({ forceUnlock = false, selectNearestIfEmpty = false } = {}) {
    if (!this.surveyOpen || !this.mapController) return;
    if (forceUnlock && !this.surveyCardDirty) {
      this.surveySelectionLocked = false;
    }
    if (!forceUnlock && (this.surveyCardDirty || this.surveySelectionLocked)) {
      this._syncSurveyMapOverlay();
      return;
    }
    const vp = this.mapController.getCurrentViewport?.() || { lng: -95.3635, lat: 29.7595 };
    const originLng = Number.isFinite(Number(this.surveyGpsFix?.lng))
      ? Number(this.surveyGpsFix.lng)
      : Number(vp.lng);
    const originLat = Number.isFinite(Number(this.surveyGpsFix?.lat))
      ? Number(this.surveyGpsFix.lat)
      : Number(vp.lat);

    const rawFeatures =
      this.mapController.getNearbyFeaturesAroundPoint?.(originLng, originLat, 130) || [];
    const surveyedKeys = this._getSurveyedKeysSet();
    this.surveyCandidates = rankNearbySurveyCandidates(rawFeatures, originLng, originLat, {
      surveyedKeys,
      maxResults: 20,
      maxRadiusMeters: 135,
    });

    if (
      selectNearestIfEmpty &&
      !this.surveyActiveCandidate &&
      this.surveyCandidates.length > 0
    ) {
      const firstUnsurveyed =
        this.surveyCandidates.find((c) => !c.isSurveyed) || this.surveyCandidates[0];
      this._loadCandidateIntoSurveyCard(firstUnsurveyed, { panMap: false, lockSelection: false });
      return;
    }

    this._renderSurveyNearbyStrip();
    this._syncSurveyMapOverlay();
  }

  _candidateFromRawProps(props = {}) {
    const address = String(props.address || props.site_addr || "").trim();
    const hcad = normalizeHcad13Clean(props.hcad_num || props.HCAD_NUM);
    const bid = String(props.building_id || props.id || "").trim();
    const { houseNumber, streetRest, sideParity } = splitAddressHouseAndStreet(address);
    const yearNum = Number(props.year_built);
    const vp = this.mapController?.getCurrentViewport?.() || { lng: -95.3635, lat: 29.7595 };
    const lng = Number.isFinite(Number(props.lng ?? props.lon))
      ? Number(props.lng ?? props.lon)
      : Number(vp.lng);
    const lat = Number.isFinite(Number(props.lat)) ? Number(props.lat) : Number(vp.lat);
    return {
      key: bid || hcad || normalizeComparableAddress(address) || `pt_${lng.toFixed(5)}_${lat.toFixed(5)}`,
      building_id: bid,
      hcad_num: hcad,
      address,
      houseNumber,
      streetDisplay: streetRest || address || (hcad ? `HCAD ${hcad}` : "Selected Site"),
      streetStem: normalizeStreetStemForAliases(address),
      sideParity,
      building_name: String(props.building_name || props.name || "").trim(),
      district: String(props.historic_district || props.district || "").trim(),
      contributing: String(props.contributing || "").trim(),
      year_built: Number.isFinite(yearNum) && yearNum >= MIN_VALID_YEAR ? yearNum : null,
      lng: round6(lng),
      lat: round6(lat),
      distanceMeters: 0,
      distanceFeet: 0,
      bearingDeg: 0,
      cardinal: "",
      isSurveyed: false,
      properties: props,
    };
  }

  _handleSurveyMapTap({ props, lngLat } = {}) {
    if (props && (props.hcad_num || props.address || props.building_id)) {
      const mergedProps = {
        ...props,
        lng: Number.isFinite(Number(props.lng ?? props.lon))
          ? Number(props.lng ?? props.lon)
          : lngLat?.lng,
        lat: Number.isFinite(Number(props.lat)) ? Number(props.lat) : lngLat?.lat,
      };
      const cand = this._candidateFromRawProps(mergedProps);
      if (!this.surveyCandidates.some((c) => c.key === cand.key)) {
        this.surveyCandidates = [cand, ...this.surveyCandidates].slice(0, 24);
      }
      this._loadCandidateIntoSurveyCard(cand, { panMap: false, lockSelection: true });
      if (this.surveySnap === "peek") this.setFieldSurveySnap("half");
      return;
    }
    if (lngLat && Number.isFinite(Number(lngLat.lng)) && Number.isFinite(Number(lngLat.lat))) {
      const near =
        this.mapController?.getNearbyFeaturesAroundPoint?.(lngLat.lng, lngLat.lat, 32) || [];
      const ranked = rankNearbySurveyCandidates(near, lngLat.lng, lngLat.lat, {
        surveyedKeys: this._getSurveyedKeysSet(),
        maxResults: 4,
        maxRadiusMeters: 32,
      });
      if (ranked.length > 0) {
        this._loadCandidateIntoSurveyCard(ranked[0], { panMap: false, lockSelection: true });
        if (this.surveySnap === "peek") this.setFieldSurveySnap("half");
        return;
      }
      // Blank lot tap: set manual coordinates so surveyor can enter street address directly
      const blankCand = this._candidateFromRawProps({
        address: "",
        lng: lngLat.lng,
        lat: lngLat.lat,
      });
      this._loadCandidateIntoSurveyCard(blankCand, { panMap: false, lockSelection: true });
      document.getElementById("survey-manual-address-wrap")?.classList.remove("hidden");
    }
  }

  selectSurveyHouseAtMapCenter() {
    if (!this.mapController) return;
    // Sample directly under the visual reticle ring center when rendered; fallback to upper map center
    let aimLngLat = null;
    if (typeof window !== "undefined") {
      const reticleRing =
        typeof document !== "undefined"
          ? document.querySelector("#field-survey-crosshair .field-survey-reticle-ring")
          : null;
      const ringRect = reticleRing ? reticleRing.getBoundingClientRect() : null;
      if (ringRect && ringRect.width > 0 && ringRect.height > 0) {
        const cx = ringRect.left + ringRect.width * 0.5;
        const cy = ringRect.top + ringRect.height * 0.5;
        // First check rendered footprint directly under reticle pixel for exact roof hit
        const directHit = this.mapController.queryBuildingAtScreenPoint?.(cx, cy);
        aimLngLat = this.mapController.screenPointToLngLat?.(cx, cy);
        if (directHit) {
          this._handleSurveyMapTap({ props: directHit, lngLat: aimLngLat });
          return;
        }
      } else {
        const sheet = document.getElementById("field-survey-sheet");
        const sheetH = sheet ? sheet.getBoundingClientRect().height : 0;
        const visibleMapH = Math.max(120, window.innerHeight - sheetH);
        aimLngLat = this.mapController.screenPointToLngLat?.(
          window.innerWidth * 0.5,
          visibleMapH * 0.5
        );
      }
    }
    if (!aimLngLat) {
      const vp = this.mapController.getCurrentViewport?.();
      aimLngLat = vp ? { lng: vp.lng, lat: vp.lat } : null;
    }
    if (!aimLngLat) return;
    this._handleSurveyMapTap({ props: null, lngLat: aimLngLat });
  }

  _loadCandidateIntoSurveyCard(candidate, { panMap = true, lockSelection = true } = {}) {
    if (!candidate) return;
    void this._disposeUnsavedSurveyDraftPhoto();
    const prevSurveyor =
      document.getElementById("survey-input-surveyor")?.value || this.surveyDraft.surveyor_name;
    const prevDistrict =
      document.getElementById("survey-input-district")?.value || this.surveyDraft.district;

    this.surveyActiveCandidate = candidate;
    this.surveySelectionLocked = Boolean(lockSelection);
    this.surveyCardDirty = false;
    this.surveyManualCondOverride = false;
    this.surveyManualContribOverride = false;

    const district =
      prevDistrict ||
      (candidate.district &&
      candidate.district !== "Outside Historic District" &&
      candidate.district !== "Outside City District"
        ? candidate.district
        : "");
    const inDistrict = Boolean(district);
    const defaultContrib = SURVEY_CONTRIBUTING_VALUES.includes(candidate.contributing)
      ? candidate.contributing
      : inDistrict
      ? "Contributing"
      : "Outside Historic District";

    this.surveyDraft = {
      surveyor_name: prevSurveyor,
      district,
      hcad_num: candidate.hcad_num || "",
      building_id: candidate.building_id || "",
      address: candidate.address || "",
      integrity_grade: "",
      condition_rating: "Good",
      contributing_status: defaultContrib,
      window_material: "",
      cladding_material: "",
      porch_alteration: "",
      roof_alteration: "",
      observed_year: "",
      demolition_status: "",
      photo_url: "",
      photo_filename: "",
      photo_blob_saved: false,
      _tempPhotoId: "",
      _photoPreviewUrl: "",
      field_notes: "",
    };

    const distEl = document.getElementById("survey-input-district");
    if (distEl && district && !distEl.value) distEl.value = district;
    const manualWrap = document.getElementById("survey-manual-address-wrap");
    const manualInput = document.getElementById("survey-input-manual-address");
    if (manualInput) manualInput.value = candidate.address || "";
    if (manualWrap) {
      manualWrap.classList.toggle("hidden", Boolean(candidate.address));
    }

    this._updateSurveyActiveHeader();
    this._syncSurveyFormControls();
    this._renderSurveyPhotoPreview();
    this._renderSurveyNearbyStrip();
    this._syncSurveyMapOverlay();

    if (panMap && Number.isFinite(candidate.lng) && Number.isFinite(candidate.lat)) {
      const vp = this.mapController?.getCurrentViewport?.() || { zoom: 18.8 };
      this.mapController?.flyToLocation?.({
        lng: candidate.lng,
        lat: candidate.lat,
        zoom: Math.max(Number(vp.zoom || 0), 18.8),
        pitch: 0,
      });
    }
  }

  _updateSurveyActiveHeader() {
    const numEl = document.getElementById("survey-active-house-num");
    const streetEl = document.getElementById("survey-active-street");
    const metaEl = document.getElementById("survey-active-meta");
    const addr = this.surveyDraft.address || this.surveyActiveCandidate?.address || "";
    const { houseNumber, streetRest } = splitAddressHouseAndStreet(addr);

    if (numEl) {
      numEl.textContent = houseNumber || (this.surveyDraft.hcad_num ? "PARCEL" : "AIM");
    }
    if (streetEl) {
      streetEl.textContent =
        streetRest ||
        addr ||
        (this.surveyDraft.hcad_num
          ? `HCAD ${this.surveyDraft.hcad_num}`
          : "Tap any roof on map or Select Center Roof");
    }
    if (metaEl) {
      const parts = [];
      if (this.surveyActiveCandidate?.year_built) {
        parts.push(`Atlas: ${this.surveyActiveCandidate.year_built}`);
      } else {
        parts.push("Atlas: Undated");
      }
      if (this.surveyDraft.hcad_num) {
        parts.push(`HCAD ${this.surveyDraft.hcad_num}`);
      }
      if (this.surveyActiveCandidate?.contributing) {
        parts.push(this.surveyActiveCandidate.contributing);
      }
      metaEl.textContent = parts.join(" · ");
    }
  }

  _selectSurveyIntegrityGrade(grade) {
    this.surveyDraft.integrity_grade = grade;
    this.surveyCardDirty = true;
    const inDist = Boolean(
      this.surveyDraft.district &&
        this.surveyDraft.district !== "Outside Historic District" &&
        this.surveyDraft.district !== AUDIT_UNDESIGNATED_DISTRICT
    );
    const defs = deriveExpressSurveyDefaults(grade, inDist);
    if (!this.surveyManualCondOverride) {
      this.surveyDraft.condition_rating = defs.condition_rating;
    }
    if (!this.surveyManualContribOverride) {
      this.surveyDraft.contributing_status = defs.contributing_status;
    }
    if (grade === "Demolished / Vacant Lot" || grade === "Vacant / Boarded") {
      this.surveyDraft.demolition_status = grade;
    } else {
      this.surveyDraft.demolition_status = "Standing";
    }
    this._syncSurveyFormControls();
  }

  _syncSurveyFormControls() {
    document
      .getElementById("survey-integrity-grid")
      ?.querySelectorAll("[data-integrity-grade]")
      .forEach((btn) => {
        const active = btn.getAttribute("data-integrity-grade") === this.surveyDraft.integrity_grade;
        btn.classList.toggle("active", active);
        btn.setAttribute("aria-pressed", active ? "true" : "false");
      });

    document
      .getElementById("survey-condition-pills")
      ?.querySelectorAll("[data-condition-rating]")
      .forEach((btn) => {
        const active =
          btn.getAttribute("data-condition-rating") === this.surveyDraft.condition_rating;
        btn.classList.toggle("active", active);
        btn.setAttribute("aria-pressed", active ? "true" : "false");
      });

    document
      .getElementById("survey-contributing-pills")
      ?.querySelectorAll("[data-contrib-status]")
      .forEach((btn) => {
        const active =
          btn.getAttribute("data-contrib-status") === this.surveyDraft.contributing_status;
        btn.classList.toggle("active", active);
        btn.setAttribute("aria-pressed", active ? "true" : "false");
      });

    const setVal = (id, v) => {
      const el = document.getElementById(id);
      if (el && el.value !== String(v ?? "")) el.value = String(v ?? "");
    };
    setVal("survey-select-windows", this.surveyDraft.window_material);
    setVal("survey-select-cladding", this.surveyDraft.cladding_material);
    setVal("survey-select-porch", this.surveyDraft.porch_alteration);
    setVal("survey-input-observed-year", this.surveyDraft.observed_year);
    setVal("survey-input-photo-url", this.surveyDraft.photo_url);
    setVal("survey-input-notes", this.surveyDraft.field_notes);

    this._updateSurveyDetailSummaryText();
    const errEl = document.getElementById("survey-inline-error");
    if (errEl) errEl.classList.add("hidden");
  }

  _updateSurveyDetailSummaryText() {
    const sumEl = document.getElementById("survey-detail-summary-meta");
    if (!sumEl) return;
    const bits = [
      `Condition: ${this.surveyDraft.condition_rating || "Good"}`,
      this.surveyDraft.contributing_status || "Contributing",
    ];
    if (this.surveyDraft.photo_filename || this.surveyDraft.photo_url) {
      bits.push("Photo attached");
    }
    if (this.surveyDraft.field_notes) {
      bits.push("Notes added");
    }
    sumEl.textContent = bits.join(" · ");
  }

  _renderSurveyNearbyStrip() {
    const host = document.getElementById("survey-nearby-strip");
    if (!host) return;
    if (!this.surveyCandidates.length) {
      host.innerHTML = `<div class="survey-nearby-empty">Pan over a street block at z=18+ and tap Refresh Nearby or tap any house roof directly on the map.</div>`;
      return;
    }
    const surveyedKeys = this._getSurveyedKeysSet();
    const activeKey = this.surveyActiveCandidate?.key || "";
    host.innerHTML = this.surveyCandidates
      .map((c) => {
        const done =
          c.isSurveyed ||
          surveyedKeys.has(c.key) ||
          Boolean(c.hcad_num && surveyedKeys.has(c.hcad_num));
        const isActive = c.key === activeKey;
        return `<button type="button" class="survey-house-chip${isActive ? " active" : ""}${
          done ? " surveyed" : ""
        }" data-candidate-key="${escHtml(c.key)}">
          <span class="chip-top">
            <strong class="chip-num">${escHtml(
              c.houseNumber || (c.hcad_num ? c.hcad_num.slice(-4) : "Site")
            )}</strong>
            <span class="chip-dist">${
              done ? "&#10003; Done" : `${c.distanceFeet}ft ${escHtml(c.cardinal)}`
            }</span>
          </span>
          <span class="chip-street">${escHtml(c.streetDisplay)}</span>
        </button>`;
      })
      .join("");
    host.querySelectorAll("[data-candidate-key]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const k = btn.getAttribute("data-candidate-key");
        const found = this.surveyCandidates.find((c) => c.key === k);
        if (found) {
          this._loadCandidateIntoSurveyCard(found, { panMap: true, lockSelection: true });
        }
      });
    });
  }

  _syncSurveyMapOverlay() {
    if (!this.surveyOpen || !this.mapController?.setFieldSurveyOverlay) return;
    const records = getLocalFieldSurveys();
    const fc = buildFieldSurveyMapOverlayGeoJson({
      gpsFix: this.surveyGpsFix,
      surveyedRecords: records,
      activeCandidate: this.surveyActiveCandidate,
    });
    this.mapController.setFieldSurveyOverlay(fc);
  }

  async _compressPhotoFileToJpegBlob(file, maxEdgePx = 1600, quality = 0.82) {
    if (typeof document === "undefined" || typeof Image === "undefined") return file;
    return new Promise((resolve) => {
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => {
        try {
          let w = img.naturalWidth || img.width || 1200;
          let h = img.naturalHeight || img.height || 900;
          if (w > maxEdgePx || h > maxEdgePx) {
            const ratio = Math.min(maxEdgePx / w, maxEdgePx / h);
            w = Math.max(1, Math.round(w * ratio));
            h = Math.max(1, Math.round(h * ratio));
          }
          const canvas = document.createElement("canvas");
          canvas.width = w;
          canvas.height = h;
          const ctx = canvas.getContext("2d");
          ctx.drawImage(img, 0, 0, w, h);
          URL.revokeObjectURL(url);
          canvas.toBlob(
            (blob) => resolve(blob || file),
            "image/jpeg",
            quality
          );
        } catch (_err) {
          URL.revokeObjectURL(url);
          resolve(file);
        }
      };
      img.onerror = () => {
        URL.revokeObjectURL(url);
        resolve(file);
      };
      img.src = url;
    });
  }

  async _disposeUnsavedSurveyDraftPhoto() {
    if (!this.surveyDraft) return;
    if (this.surveyDraft._photoPreviewUrl) {
      try {
        URL.revokeObjectURL(this.surveyDraft._photoPreviewUrl);
      } catch (_err) {
        // Ignore URL revoke errors
      }
    }
    const unsavedId = this.surveyDraft._tempPhotoId || "";
    this.surveyDraft._photoPreviewUrl = "";
    this.surveyDraft._tempPhotoId = "";
    this.surveyDraft.photo_filename = "";
    this.surveyDraft.photo_blob_saved = false;
    if (unsavedId) {
      const savedInQueue = getLocalFieldSurveys().some(
        (r) => r.survey_id === unsavedId || r.local_photo_id === unsavedId
      );
      if (!savedInQueue) {
        await deleteSurveyPhotoBlob(unsavedId);
      }
    }
  }

  async _handleSurveyPhotoFile(file) {
    await this._disposeUnsavedSurveyDraftPhoto();
    const filename = buildSurveyPhotoFilename(
      this.surveyDraft.hcad_num,
      this.surveyDraft.address
    );
    const compressed = await this._compressPhotoFileToJpegBlob(file);
    const tempId = formatSurveyId(this.surveyDraft.hcad_num || "photo");
    const saved = await saveSurveyPhotoBlob({
      photoId: tempId,
      surveyId: tempId,
      blob: compressed,
      filename,
      mimeType: compressed.type || "image/jpeg",
      hcadNum: this.surveyDraft.hcad_num,
      address: this.surveyDraft.address,
    });
    let previewUrl = "";
    try {
      previewUrl = URL.createObjectURL(compressed);
    } catch (_err) {
      previewUrl = "";
    }
    this.surveyDraft._tempPhotoId = tempId;
    this.surveyDraft._photoPreviewUrl = previewUrl;
    this.surveyDraft.photo_filename = filename;
    this.surveyDraft.photo_blob_saved = Boolean(saved?.ok);
    this.surveyCardDirty = true;
    this._renderSurveyPhotoPreview();
    this._updateSurveyDetailSummaryText();
    await this._refreshSurveyPhotoCounter();
  }

  _renderSurveyPhotoPreview() {
    const wrap = document.getElementById("survey-photo-preview-wrap");
    const img = document.getElementById("survey-photo-preview-img");
    const nameEl = document.getElementById("survey-photo-preview-name");
    if (!wrap) return;
    if (this.surveyDraft.photo_filename) {
      wrap.classList.remove("hidden");
      if (img) {
        if (this.surveyDraft._photoPreviewUrl) {
          img.src = this.surveyDraft._photoPreviewUrl;
          img.classList.remove("hidden");
        } else {
          img.classList.add("hidden");
        }
      }
      if (nameEl) {
        nameEl.textContent = `${this.surveyDraft.photo_filename} (saved on device)`;
      }
    } else {
      wrap.classList.add("hidden");
    }
  }

  async _refreshSurveyPhotoCounter() {
    this.surveyPhotoSummaries = await listSurveyPhotoSummaries();
    const btn = document.getElementById("btn-survey-share-photos");
    if (btn) {
      const n = this.surveyPhotoSummaries.length;
      btn.textContent = n > 0 ? `Share / Save Photos (${n})` : "Share / Save Photos (0)";
      btn.disabled = n === 0;
    }
  }

  async saveCurrentSurveyObservation({ advanceNext = true } = {}) {
    this.surveyDraft.surveyor_name = String(
      document.getElementById("survey-input-surveyor")?.value || this.surveyDraft.surveyor_name || ""
    ).trim();
    this.surveyDraft.district = String(
      document.getElementById("survey-input-district")?.value || this.surveyDraft.district || ""
    ).trim();
    if (!this.surveyDraft.address) {
      this.surveyDraft.address = String(
        document.getElementById("survey-input-manual-address")?.value || ""
      ).trim();
    }

    const cand = this.surveyActiveCandidate;
    const vp = this.mapController?.getCurrentViewport?.() || { lng: -95.3635, lat: 29.7595 };
    const recLng = Number.isFinite(Number(cand?.lng))
      ? Number(cand.lng)
      : Number.isFinite(Number(this.surveyGpsFix?.lng))
      ? Number(this.surveyGpsFix.lng)
      : Number(vp.lng);
    const recLat = Number.isFinite(Number(cand?.lat))
      ? Number(cand.lat)
      : Number.isFinite(Number(this.surveyGpsFix?.lat))
      ? Number(this.surveyGpsFix.lat)
      : Number(vp.lat);

    const draftCheck = validateFieldSurveyDraft({
      ...this.surveyDraft,
      gps_lng: round6(recLng),
      gps_lat: round6(recLat),
    });
    const errEl = document.getElementById("survey-inline-error");
    if (!draftCheck.ok) {
      if (errEl) {
        errEl.textContent = draftCheck.error;
        errEl.classList.remove("hidden");
      }
      if (draftCheck.field === "surveyor_name") {
        const survInp = document.getElementById("survey-input-surveyor");
        survInp?.scrollIntoView?.({ block: "nearest" });
        survInp?.focus?.();
      } else if (draftCheck.field === "address") {
        document.getElementById("survey-manual-address-wrap")?.classList.remove("hidden");
        const addrInp = document.getElementById("survey-input-manual-address");
        addrInp?.scrollIntoView?.({ block: "nearest" });
        addrInp?.focus?.();
      } else if (draftCheck.field === "observed_year") {
        const det = document.getElementById("survey-detail-drawer");
        if (det) det.open = true;
        const yrInp = document.getElementById("survey-input-observed-year");
        yrInp?.scrollIntoView?.({ block: "nearest" });
        yrInp?.focus?.();
      } else {
        errEl?.scrollIntoView?.({ block: "nearest" });
      }
      return null;
    }
    if (errEl) errEl.classList.add("hidden");

    const surveyId =
      this.surveyDraft._tempPhotoId ||
      formatSurveyId(this.surveyDraft.hcad_num || this.surveyDraft.address);
    const noteWithPhoto =
      this.surveyDraft.photo_filename &&
      !String(this.surveyDraft.field_notes || "").includes(this.surveyDraft.photo_filename)
        ? `${this.surveyDraft.field_notes ? `${this.surveyDraft.field_notes} ` : ""}[PHOTO CAPTURED: ${
            this.surveyDraft.photo_filename
          }]`
        : this.surveyDraft.field_notes;

    const gpsAccNum = Number(this.surveyGpsFix?.accuracy);
    const gpsAccClean = Number.isFinite(gpsAccNum) ? Number(gpsAccNum.toFixed(1)) : "";
    const gpsLat6 = round6(recLat);
    const gpsLng6 = round6(recLng);
    const gpsLatLngAcc = `${gpsLat6.toFixed(6)}, ${gpsLng6.toFixed(6)}${
      gpsAccClean !== "" ? ` (+/-${Math.round(gpsAccClean)}m)` : ""
    }`;
    const obsYrStr = String(this.surveyDraft.observed_year || "").trim();
    const demoStr =
      this.surveyDraft.demolition_status && this.surveyDraft.demolition_status !== "Standing"
        ? this.surveyDraft.demolition_status
        : "";
    const obsYearOrDemo = [obsYrStr, demoStr].filter(Boolean).join(" | ");
    const atlasYearBuilt =
      Number.isFinite(Number(cand?.year_built)) && Number(cand.year_built) > 0
        ? Math.round(Number(cand.year_built))
        : "";

    const saveRes = saveLocalFieldSurvey({
      survey_id: surveyId,
      surveyed_at: new Date().toISOString().slice(0, 19).replace("T", " "),
      review_status: "Unreviewed",
      hcad_num: this.surveyDraft.hcad_num,
      building_id: this.surveyDraft.building_id,
      address: this.surveyDraft.address,
      district_or_corridor: this.surveyDraft.district || AUDIT_UNDESIGNATED_DISTRICT,
      integrity_grade: this.surveyDraft.integrity_grade,
      condition_rating: this.surveyDraft.condition_rating,
      suggested_contributing: this.surveyDraft.contributing_status,
      windows_status: this.surveyDraft.window_material,
      cladding_material: this.surveyDraft.cladding_material,
      porch_integrity: this.surveyDraft.porch_alteration,
      atlas_year_built: atlasYearBuilt,
      observed_year_or_demo_note: obsYearOrDemo,
      photo_url: this.surveyDraft.photo_url,
      field_notes: noteWithPhoto,
      surveyor_name: this.surveyDraft.surveyor_name,
      gps_lat_lng_acc: gpsLatLngAcc,
      gps_lat: gpsLat6,
      gps_lng: gpsLng6,
      gps_accuracy_m: gpsAccClean,
      ...(this.surveyDraft._tempPhotoId ? { local_photo_id: this.surveyDraft._tempPhotoId } : {}),
      ...(this.surveyDraft.photo_filename
        ? { local_photo_filename: this.surveyDraft.photo_filename }
        : {}),
      sync_status: "queued",
    });
    const record = saveRes.record;

    this.surveyLastSavedId = record.survey_id;
    this.surveyCardDirty = false;
    const savedCandSnapshot = cand ? { ...cand } : null;

    if (cand) cand.isSurveyed = true;
    this._refreshSurveyQueueUi();
    this._syncSurveyMapOverlay();
    this._showSurveyUndoToast(
      `Saved ${record.address || record.hcad_num} (${record.integrity_grade})`,
      savedCandSnapshot
    );

    // Defer opportunistic background webhook sync until after the 5.5s Undo grace window
    const webhookUrl = this._getWebhookUrl();
    const isOnline = typeof navigator === "undefined" || navigator.onLine !== false;
    if (webhookUrl && isOnline) {
      this._scheduleDeferredSurveySync(record, webhookUrl);
    }

    if (advanceNext) {
      const nextCand = pickNextBlockfaceCandidate(this.surveyCandidates, cand, {
        walkingBearingDeg: this.surveyWalkingBearingDeg,
        surveyedKeys: this._getSurveyedKeysSet(),
      });
      if (nextCand && cand) {
        this.surveyWalkingBearingDeg = compassBearingDegrees(
          cand.lng,
          cand.lat,
          nextCand.lng,
          nextCand.lat
        );
      }
      if (nextCand) {
        this._loadCandidateIntoSurveyCard(nextCand, { panMap: true });
        document
          .querySelector("#field-survey-sheet .field-survey-scroll-body")
          ?.scrollTo?.({ top: 0, behavior: "auto" });
      } else {
        this._renderSurveyNearbyStrip();
        this._toast("All visible nearby houses surveyed! Walk forward and tap Refresh Nearby.");
      }
    } else {
      this._renderSurveyNearbyStrip();
    }
    return record;
  }

  _showSurveyUndoToast(message, prevCandidateSnapshot) {
    const toast = document.getElementById("field-survey-toast");
    const msgEl = document.getElementById("field-survey-toast-msg");
    this._lastUndoCandidateSnapshot = prevCandidateSnapshot || null;
    if (!toast || !msgEl) return;
    msgEl.textContent = message;
    toast.classList.remove("hidden");
    if (this._surveyToastTimer) clearTimeout(this._surveyToastTimer);
    this._surveyToastTimer = setTimeout(() => {
      toast.classList.add("hidden");
    }, 5500);
  }

  _scheduleDeferredSurveySync(record, webhookUrl, delayMs = 5600) {
    const sid = record?.survey_id;
    if (!sid || !webhookUrl) return;
    const prev = this._deferredSurveySyncTimers.get(sid);
    if (prev) clearTimeout(prev.timerId);
    const runNow = () => {
      this._deferredSurveySyncTimers.delete(sid);
      const stillQueued = getLocalFieldSurveys().find((r) => r.survey_id === sid);
      if (!stillQueued || stillQueued.sync_status === "synced") return;
      submitFieldSurveyObservation(stillQueued, webhookUrl, { requireExistingLocal: true })
        .then(() => {
          if (this.surveyOpen) {
            this._refreshSurveyQueueUi();
            this._syncSurveyMapOverlay();
          }
        })
        .catch(() => {});
    };
    const timerId = setTimeout(runNow, delayMs);
    this._deferredSurveySyncTimers.set(sid, { timerId, runNow });
  }

  _cancelDeferredSurveySync(surveyId) {
    const entry = this._deferredSurveySyncTimers.get(surveyId);
    if (entry) {
      clearTimeout(entry.timerId);
      this._deferredSurveySyncTimers.delete(surveyId);
    }
  }

  _flushDeferredSurveySyncTimers({ fireImmediately = true } = {}) {
    const entries = Array.from(this._deferredSurveySyncTimers.values());
    this._deferredSurveySyncTimers.clear();
    for (const entry of entries) {
      clearTimeout(entry.timerId);
      if (fireImmediately) {
        entry.runNow();
      }
    }
  }

  undoLastSurveySave() {
    if (!this.surveyLastSavedId) return;
    const id = this.surveyLastSavedId;
    this.surveyLastSavedId = "";
    this._cancelDeferredSurveySync(id);
    if (this._surveyToastTimer) {
      clearTimeout(this._surveyToastTimer);
      this._surveyToastTimer = null;
    }
    const remRes = removeLocalFieldSurvey(id);
    const removedPhotoId = remRes?.removed?.local_photo_id || "";
    if (removedPhotoId) {
      void deleteSurveyPhotoBlob(removedPhotoId).then(() => this._refreshSurveyPhotoCounter());
    }
    document.getElementById("field-survey-toast")?.classList.add("hidden");
    if (this._lastUndoCandidateSnapshot) {
      this._lastUndoCandidateSnapshot.isSurveyed = false;
      const inList = this.surveyCandidates.find(
        (c) => c.key === this._lastUndoCandidateSnapshot.key
      );
      if (inList) inList.isSurveyed = false;
      this._loadCandidateIntoSurveyCard(this._lastUndoCandidateSnapshot, { panMap: true });
    }
    this._refreshSurveyQueueUi();
    this._renderSurveyNearbyStrip();
    this._syncSurveyMapOverlay();
    this._toast("Undid last survey entry.");
  }

  _refreshSurveyQueueUi() {
    const list = getLocalFieldSurveys();
    const syncedCount = list.filter((r) => r.sync_status === "synced").length;
    const pendingCount = list.length - syncedCount;

    const badge = document.getElementById("btn-survey-sync-badge");
    if (badge) {
      badge.textContent =
        pendingCount > 0 ? `Offline Queue: ${pendingCount}` : `Saved: ${list.length}`;
      badge.classList.toggle("has-pending", pendingCount > 0);
    }
    const summaryEl = document.getElementById("survey-queue-summary-text");
    if (summaryEl) {
      summaryEl.textContent =
        `${list.length} total observation${list.length === 1 ? "" : "s"} on device ` +
        `(${syncedCount} synced to Google Sheet, ${pendingCount} queued offline).`;
    }
    const syncBtn = document.getElementById("btn-survey-sync-now");
    if (syncBtn) syncBtn.disabled = pendingCount === 0;
    const csvBtn = document.getElementById("btn-survey-export-csv");
    if (csvBtn) csvBtn.disabled = list.length === 0;
  }

  async syncOfflineSurveyQueueNow() {
    this._flushDeferredSurveySyncTimers({ fireImmediately: false });
    const webhookUrl = this._getWebhookUrl();
    const statusEl = document.getElementById("survey-queue-sync-status");
    if (!webhookUrl) {
      if (statusEl) {
        statusEl.textContent =
          "No Apps Script Webhook URL configured yet. Export CSV below or configure Webhook in Staff Admin.";
        statusEl.classList.remove("hidden");
      }
      return;
    }
    if (statusEl) {
      statusEl.textContent = "Syncing offline field observations to Google Sheet...";
      statusEl.classList.remove("hidden");
    }
    const res = await flushPendingFieldSurveys(webhookUrl);
    this._refreshSurveyQueueUi();
    this._syncSurveyMapOverlay();
    if (statusEl) {
      const lastError = res.errors?.[0]?.error || "offline";
      statusEl.textContent =
        res.remainingCount === 0
          ? `Synced ${res.syncedCount} observation${res.syncedCount === 1 ? "" : "s"} to Google Sheet.`
          : `Synced ${res.syncedCount} of ${res.totalPendingBefore}; ${res.remainingCount} kept in offline queue (${lastError}).`;
    }
  }

  downloadSurveyQueueCsv() {
    const list = getLocalFieldSurveys();
    if (!list.length) {
      this._toast("No field survey records saved yet.");
      return;
    }
    const csv = exportFieldSurveysToCsv(list);
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, "");
    a.href = url;
    a.download = `houston_field_surveys_${stamp}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    this._toast(`Downloaded formula-safe CSV (${list.length} survey rows).`);
  }

  async shareOrDownloadQueuedSurveyPhotos() {
    const summaries = await listSurveyPhotoSummaries();
    if (!summaries.length) {
      this._toast("No field survey photos stored on device yet.");
      return;
    }
    const files = [];
    for (const s of summaries.slice(0, 20)) {
      const pid = s.photoId || s.surveyId || s.survey_id;
      const entry = await getSurveyPhotoBlob(pid);
      if (entry?.blob) {
        try {
          files.push(
            new File([entry.blob], entry.filename || `${pid}.jpg`, {
              type: entry.mimeType || "image/jpeg",
            })
          );
        } catch (_err) {
          // Fallback to anchor download below
        }
      }
    }
    if (
      files.length > 0 &&
      typeof navigator !== "undefined" &&
      typeof navigator.canShare === "function" &&
      navigator.canShare({ files })
    ) {
      try {
        await navigator.share({
          title: "Preservation Houston Field Survey Photos",
          files,
        });
        return;
      } catch (err) {
        if (err?.name === "AbortError") {
          return;
        }
        // Web Share unsupported for these files; fall through to download
      }
    }
    for (const f of files) {
      const url = URL.createObjectURL(f);
      const a = document.createElement("a");
      a.href = url;
      a.download = f.name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1500);
    }
    this._toast(`Exported ${files.length} named survey photo${files.length === 1 ? "" : "s"}.`);
  }
}
