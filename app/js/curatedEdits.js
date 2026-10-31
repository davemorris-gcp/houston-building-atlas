/**
 * Curated Archival Overrides & Google Sheets Live Synchronization Module
 * for The Houston Building Atlas v2 (Preservation Houston).
 *
 * Architecture:
 * 1. Baseline overrides live in `public/data/curated_overrides.json` (zero-cost static JSON).
 * 2. Live admin edits can be published from a Google Sheet (`Approved_Edits` tab -> CSV)
 *    and fetched client-side over CORS on startup without rebuilding the PMTiles shards.
 * 3. End-user correction suggestions ("Suggest a Correction") are submitted with
 *    `status = "Pending"` to a Google Apps Script webhook feeding the `Pending_Submissions`
 *    tab of the same Google Sheet. Suggestions NEVER go live on the public map until a
 *    Preservation Houston administrator changes the row's status dropdown to `Approved`.
 */

const LS_SHEET_CSV_URL_KEY = "ph_atlas_sheet_csv_url";
const LS_WEBHOOK_URL_KEY = "ph_atlas_submission_webhook_url";
const LS_PENDING_SUGGESTIONS_KEY = "ph_atlas_pending_suggestions";
const LS_HIDDEN_CLAIMS_KEY = "ph_atlas_hidden_claims";
export const LS_FIELD_SURVEYS_KEY = "ph_atlas_field_surveys_v1";
export const IDB_FIELD_SURVEY_PHOTOS_DB = "ph_atlas_field_survey_photos_v1";
const IDB_FIELD_SURVEY_PHOTOS_STORE = "photos";

export const FLAG_SOURCE_TYPES = new Set([
  "PHOTO_MISATTRIBUTION_FLAG",
  "CLAIM_DISPUTE_FLAG",
  "NARRATIVE_ERROR_FLAG",
]);

export const MIN_VALID_YEAR = 1836;
export const MAX_VALID_YEAR = 2026;
export const MAX_STANDING_OVERRIDE_YEAR = 2035;

// Milestone 3 cross-language domain constants (kept 1:1 with pipeline/atlas_pipeline/schema.py)
export const HARRIS_COUNTY_BBOX = Object.freeze([-96.2, 29.3, -94.8, 30.3]);
export const HARRIS_COUNTY_BOUNDS = Object.freeze({
  minLng: -96.2,
  minLat: 29.3,
  maxLng: -94.8,
  maxLat: 30.3,
});
export const MIN_FOOTPRINT_AREA_SQ_M = 4.0;
export const POLYGON_EDITOR_MAX_VERTICES = 64;
export const GOOGLE_SHEETS_CELL_MAX_CHARS = 50000;
export const MAX_GEOMETRY_JSON_CELL_CHARS = 49000;
export const METERS_PER_DEGREE_LAT = 111320.0;
export const SQ_FT_PER_SQ_M = 10.76391041671;

export const GEOMETRY_TAG_PREFIX = "[GEOMETRY_GEOJSON:";
export const FIELD_SURVEY_TAG_PREFIX = "[FIELD_SURVEY_JSON:";
export const FIELD_SURVEY_SOURCE_TYPE = "FIELD_SURVEY_OBSERVATION";

export const FIELD_SURVEY_INTEGRITY_GRADES = Object.freeze([
  "Intact",
  "Minor Alterations",
  "Substantially Altered",
  "Vacant / Boarded",
  "Demolished / Vacant Lot",
  "Storm / Hazard Damaged",
]);

export const FIELD_SURVEY_CONDITION_RATINGS = Object.freeze([
  "Good",
  "Fair",
  "Poor",
  "Critical / Emergency",
]);

export const FIELD_SURVEY_CONTRIBUTING_VALUES = Object.freeze([
  "Contributing",
  "Non-Contributing",
  "Potentially Contributing",
  "Vacant / Demolished",
  "Outside Historic District",
]);

export const FIELD_SURVEY_CSV_COLUMNS = Object.freeze([
  "survey_id",
  "surveyed_at",
  "review_status",
  "hcad_num",
  "address",
  "district_or_corridor",
  "integrity_grade",
  "condition_rating",
  "suggested_contributing",
  "windows_status",
  "cladding_material",
  "porch_integrity",
  "atlas_year_built",
  "observed_year_or_demo_note",
  "photo_url",
  "field_notes",
  "surveyor_name",
  "gps_lat_lng_acc",
]);

export const OVERRIDE_MODE_SHARD_PRIMARY = "SHARD_PRIMARY_METADATA";
export const OVERRIDE_MODE_ADDITIVE_POLYGON = "ADDITIVE_CUSTOM_POLYGON";
export const OVERRIDE_MODE_PARCEL_REPLACEMENT = "PARCEL_REPLACEMENT_POLYGON";
export const OVERRIDE_MODE_SUPPRESS_VACANT = "SUPPRESS_PARCEL_VACANT";

export const VALID_CONTRIBUTING_VALUES = new Set([
  "Contributing",
  "Non-Contributing",
  "Outside Historic District",
  "Tax-Exempt Campus Structure",
]);

export const DEMOLISHED_VACANT_ISSUE = "Building demolished - lot currently vacant";
export const DEMOLISHED_REPLACED_ISSUE = "Building demolished & replaced by newer structure";
export const DEMOLISHED_VACANT_CITATION_TAG = "[DEMOLISHED / VACANT LOT - HIDE FOOTPRINT]";

const EXPLICIT_VACANT_YEAR_TOKENS = new Set([
  "0",
  "vacant",
  "vacant lot",
  "demolished",
  "none",
  "n/a",
]);

const GEOMETRY_ONLY_FALLBACK_SOURCES = new Set([
  "",
  "SATELLITE / AERIAL IMAGERY",
  "AERIAL / SATELLITE IMAGERY",
  "SATELLITE / AERIAL IMAGERY VERIFICATION",
  "SATELLITE / GIS FOOTPRINT EDIT",
  "STAFF POLYGON EDITOR",
  "PRESERVATION HOUSTON STAFF POLYGON EDITOR",
  "HCAD PARCEL GIS",
  "[FOOTPRINT FIX NEEDED]",
]);

const RE_DEMOLISHED_VACANT_TEXT =
  /\[demolished\s*\/\s*vacant\s+lot|demolished\s*[-:/]?\s*lot\s+currently\s+vacant|vacant\s+lot\s*\(hide\s+footprint\)|\bsuppress_only\b/i;
const RE_LEGACY_DEMOLISHED_ISSUE = /building\s+demolished\s+or\s+replaced/i;
const RE_VACANT_LOT_CUE = /\bvacant\b|\bcleared\b|\bempty\s+lot\b|\bno\s+replacement\b/i;

function sanitizeAtlasTextJs(raw) {
  return String(raw ?? "")
    .replace(/ \u2014 /g, " - ")
    .replace(/\u2014/g, "-")
    .replace(/ \u2013 /g, " - ")
    .replace(/\u2013/g, "-");
}

function sanitizePayloadStringsJs(val) {
  if (typeof val === "string") {
    return val.includes("\u2014") || val.includes("\u2013") ? sanitizeAtlasTextJs(val) : val;
  }
  if (Array.isArray(val)) return val.map(sanitizePayloadStringsJs);
  if (val && typeof val === "object") {
    const out = {};
    for (const [k, v] of Object.entries(val)) {
      out[k] = sanitizePayloadStringsJs(v);
    }
    return out;
  }
  return val;
}

/**
 * Classify an override record into one of the 4 DF-1 footprint disposition modes
 * (kept 1:1 with `schema.classify_override_footprint_mode` in Python).
 */
export function classifyOverrideFootprintMode(ovKey, ov) {
  if (!ov || typeof ov !== "object" || Object.keys(ov).length === 0) {
    return "";
  }
  if (ov.suppress_only) {
    return OVERRIDE_MODE_SUPPRESS_VACANT;
  }
  const keyStr = String(ovKey || ov.building_id || ov.id || "").trim();
  const isSub = keyStr.includes("#") || Boolean(ov.is_building_override);
  const geom = ov.geometry;
  const hasGeom = Boolean(
    geom &&
      typeof geom === "object" &&
      (geom.type === "Polygon" || geom.type === "MultiPolygon") &&
      Array.isArray(geom.coordinates) &&
      geom.coordinates.length > 0
  );

  if (isSub) {
    if (hasGeom) {
      return ov.replace_parcel_shards
        ? OVERRIDE_MODE_PARCEL_REPLACEMENT
        : OVERRIDE_MODE_ADDITIVE_POLYGON;
    }
    return OVERRIDE_MODE_SHARD_PRIMARY;
  }
  if (ov.keep_shard_footprints === true && !hasGeom) {
    return OVERRIDE_MODE_SHARD_PRIMARY;
  }
  return OVERRIDE_MODE_PARCEL_REPLACEMENT;
}

function projectRingToTangentMetersJs(pts, origin = null) {
  if (!Array.isArray(pts) || pts.length === 0) {
    return { xy: [], origin: [0, 0], cosLat: 1.0 };
  }
  let cLng = 0;
  let cLat = 0;
  if (Array.isArray(origin) && origin.length >= 2) {
    cLng = Number(origin[0]);
    cLat = Number(origin[1]);
  } else {
    for (const p of pts) {
      cLng += p[0];
      cLat += p[1];
    }
    cLng /= pts.length;
    cLat /= pts.length;
  }
  let cosLat = Math.cos((cLat * Math.PI) / 180);
  if (Math.abs(cosLat) < 1e-6) cosLat = 1e-6;
  const xy = pts.map(([lng, lat]) => [
    (lng - cLng) * cosLat * METERS_PER_DEGREE_LAT,
    (lat - cLat) * METERS_PER_DEGREE_LAT,
  ]);
  return { xy, origin: [cLng, cLat], cosLat };
}

function signedShoelaceAreaSqMJs(xy) {
  const n = xy.length;
  if (n < 3) return 0;
  let acc = 0;
  for (let i = 0; i < n; i++) {
    const [x1, y1] = xy[i];
    const [x2, y2] = xy[(i + 1) % n];
    acc += x1 * y2 - x2 * y1;
  }
  return 0.5 * acc;
}

function segmentsIntersectPlanarJs(a1, a2, b1, b2, eps = 1e-7) {
  const minAx = a1[0] <= a2[0] ? a1[0] : a2[0];
  const maxAx = a1[0] <= a2[0] ? a2[0] : a1[0];
  const minAy = a1[1] <= a2[1] ? a1[1] : a2[1];
  const maxAy = a1[1] <= a2[1] ? a2[1] : a1[1];
  const minBx = b1[0] <= b2[0] ? b1[0] : b2[0];
  const maxBx = b1[0] <= b2[0] ? b2[0] : b1[0];
  const minBy = b1[1] <= b2[1] ? b1[1] : b2[1];
  const maxBy = b1[1] <= b2[1] ? b2[1] : b1[1];
  if (
    maxAx + eps < minBx ||
    maxBx + eps < minAx ||
    maxAy + eps < minBy ||
    maxBy + eps < minAy
  ) {
    return false;
  }
  const cross = (o, p, q) => (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0]);
  const onSegment = (o, p, q) =>
    Math.min(o[0], q[0]) - eps <= p[0] &&
    p[0] <= Math.max(o[0], q[0]) + eps &&
    Math.min(o[1], q[1]) - eps <= p[1] &&
    p[1] <= Math.max(o[1], q[1]) + eps;

  const d1 = cross(a1, a2, b1);
  const d2 = cross(a1, a2, b2);
  const d3 = cross(b1, b2, a1);
  const d4 = cross(b1, b2, a2);

  if (
    ((d1 > eps && d2 < -eps) || (d1 < -eps && d2 > eps)) &&
    ((d3 > eps && d4 < -eps) || (d3 < -eps && d4 > eps))
  ) {
    return true;
  }
  if (Math.abs(d1) <= eps && onSegment(a1, b1, a2)) return true;
  if (Math.abs(d2) <= eps && onSegment(a1, b2, a2)) return true;
  if (Math.abs(d3) <= eps && onSegment(b1, a1, b2)) return true;
  if (Math.abs(d4) <= eps && onSegment(b1, a2, b2)) return true;
  return false;
}

function round6Js(val) {
  const r = Number(val.toFixed(6));
  return Object.is(r, -0) ? 0 : r;
}

/**
 * Validate and normalize an override Polygon in strict post-rounding order (DF-3, DF-9).
 * Kept 1:1 with `schema.validate_and_normalize_override_polygon` in Python.
 */
export function validateOverridePolygonGeometry(
  geometryOrRing,
  {
    bbox = HARRIS_COUNTY_BBOX,
    minAreaSqM = MIN_FOOTPRINT_AREA_SQ_M,
    maxVertices = POLYGON_EDITOR_MAX_VERTICES,
  } = {}
) {
  const fail = (msgs, extra = {}) => ({
    ok: false,
    geometry: null,
    vertex_count: extra.vertex_count ?? 0,
    area_sq_m: extra.area_sq_m ?? 0,
    area_sq_ft: extra.area_sq_ft ?? 0,
    centroid: extra.centroid ?? null,
    was_reversed: extra.was_reversed ?? false,
    errors: Array.isArray(msgs) ? msgs : [msgs],
  });

  let rawRing = null;
  if (Array.isArray(geometryOrRing)) {
    rawRing = geometryOrRing;
  } else if (geometryOrRing && typeof geometryOrRing === "object") {
    if (geometryOrRing.type !== "Polygon") {
      return fail("Geometry must have type 'Polygon'.");
    }
    const coords = geometryOrRing.coordinates;
    if (!Array.isArray(coords) || coords.length === 0) {
      return fail("Polygon coordinates must contain an exterior ring.");
    }
    if (coords.length > 1) {
      return fail("Polygon interior holes are not supported for building footprint overrides.");
    }
    rawRing = coords[0];
  } else {
    return fail("Geometry must be a GeoJSON Polygon object or exterior coordinate ring.");
  }

  if (!Array.isArray(rawRing) || rawRing.length < 3) {
    return fail("Polygon exterior ring requires at least 3 distinct vertices.");
  }

  const parsedRaw = [];
  const parseErrors = [];
  for (let i = 0; i < rawRing.length; i++) {
    const pt = rawRing[i];
    if (
      !Array.isArray(pt) ||
      pt.length < 2 ||
      typeof pt[0] === "boolean" ||
      typeof pt[1] === "boolean" ||
      pt[0] === null ||
      pt[1] === null ||
      pt[0] === "" ||
      pt[1] === ""
    ) {
      parseErrors.push(`Vertex ${i} is not a numeric [lng, lat] coordinate pair.`);
      continue;
    }
    const lngF = Number(pt[0]);
    const latF = Number(pt[1]);
    if (!Number.isFinite(lngF) || !Number.isFinite(latF)) {
      parseErrors.push(`Vertex ${i} has non-finite coordinates.`);
      continue;
    }
    parsedRaw.push([lngF, latF]);
  }
  if (parseErrors.length > 0) {
    return fail(parseErrors);
  }

  while (
    parsedRaw.length > 1 &&
    parsedRaw[parsedRaw.length - 1][0] === parsedRaw[0][0] &&
    parsedRaw[parsedRaw.length - 1][1] === parsedRaw[0][1]
  ) {
    parsedRaw.pop();
  }

  const snapped = [];
  for (const [lngF, latF] of parsedRaw) {
    const sLng = round6Js(lngF);
    const sLat = round6Js(latF);
    const prev = snapped[snapped.length - 1];
    if (!prev || prev[0] !== sLng || prev[1] !== sLat) {
      snapped.push([sLng, sLat]);
    }
  }
  while (
    snapped.length > 1 &&
    snapped[snapped.length - 1][0] === snapped[0][0] &&
    snapped[snapped.length - 1][1] === snapped[0][1]
  ) {
    snapped.pop();
  }

  const n = snapped.length;
  if (n < 3) {
    return fail(`Polygon requires at least 3 distinct snapped vertices (got ${n}).`, {
      vertex_count: n,
    });
  }
  if (n > maxVertices) {
    return fail(`Polygon has ${n} vertices, exceeding maximum allowed (${maxVertices}).`, {
      vertex_count: n,
    });
  }

  const [minLng, minLat, maxLng, maxLat] = bbox;
  for (let i = 0; i < n; i++) {
    const [sLng, sLat] = snapped[i];
    if (sLng < minLng || sLng > maxLng || sLat < minLat || sLat > maxLat) {
      return fail(
        `Vertex ${i} [${sLng}, ${sLat}] falls outside Harris County bounds [${minLng}, ${minLat}, ${maxLng}, ${maxLat}].`,
        { vertex_count: n }
      );
    }
  }

  const { xy, origin } = projectRingToTangentMetersJs(snapped);
  const [cLng, cLat] = origin;
  const signedArea = signedShoelaceAreaSqMJs(xy);
  const areaSqM = Math.abs(signedArea);
  const centroid = [round6Js(cLng), round6Js(cLat)];
  if (areaSqM < minAreaSqM) {
    return fail(
      `Polygon footprint area (${areaSqM.toFixed(2)} sq m) is below minimum threshold (${minAreaSqM.toFixed(1)} sq m).`,
      {
        vertex_count: n,
        area_sq_m: Number(areaSqM.toFixed(3)),
        area_sq_ft: Number((areaSqM * SQ_FT_PER_SQ_M).toFixed(1)),
        centroid,
      }
    );
  }

  let wasReversed = false;
  if (signedArea < 0) {
    snapped.reverse();
    xy.reverse();
    wasReversed = true;
  }

  const seenKeys = new Set();
  for (const [sLng, sLat] of snapped) {
    seenKeys.add(`${sLng},${sLat}`);
  }
  if (seenKeys.size !== n) {
    return fail("Polygon contains a pinched duplicate non-adjacent vertex (self-touching ring).", {
      vertex_count: n,
      area_sq_m: Number(areaSqM.toFixed(2)),
      area_sq_ft: Number((areaSqM * SQ_FT_PER_SQ_M).toFixed(1)),
      centroid,
      was_reversed: wasReversed,
    });
  }

  for (let i = 0; i < n; i++) {
    const pPrev = xy[(i - 1 + n) % n];
    const pCurr = xy[i];
    const pNext = xy[(i + 1) % n];
    const ux = pCurr[0] - pPrev[0];
    const uy = pCurr[1] - pPrev[1];
    const vx = pNext[0] - pCurr[0];
    const vy = pNext[1] - pCurr[1];
    const uLen = Math.hypot(ux, uy);
    const vLen = Math.hypot(vx, vy);
    if (uLen < 1e-6 || vLen < 1e-6) {
      return fail("Polygon contains a zero-length degenerate edge after 6-decimal coordinate rounding.", {
        vertex_count: n,
        area_sq_m: Number(areaSqM.toFixed(2)),
        area_sq_ft: Number((areaSqM * SQ_FT_PER_SQ_M).toFixed(1)),
        centroid,
        was_reversed: wasReversed,
      });
    }
    const crossVal = ux * vy - uy * vx;
    const dotVal = ux * vx + uy * vy;
    if (dotVal < 0 && Math.abs(crossVal) <= 1e-6 * uLen * vLen) {
      return fail("Polygon contains a 180-degree folded collinear edge spike.", {
        vertex_count: n,
        area_sq_m: Number(areaSqM.toFixed(2)),
        area_sq_ft: Number((areaSqM * SQ_FT_PER_SQ_M).toFixed(1)),
        centroid,
        was_reversed: wasReversed,
      });
    }
  }

  for (let i = 0; i < n; i++) {
    const a1 = xy[i];
    const a2 = xy[(i + 1) % n];
    for (let j = i + 2; j < n; j++) {
      if (i === 0 && j === n - 1) continue;
      const b1 = xy[j];
      const b2 = xy[(j + 1) % n];
      if (segmentsIntersectPlanarJs(a1, a2, b1, b2)) {
        return fail(`Polygon self-intersects between edge ${i} and edge ${j}.`, {
          vertex_count: n,
          area_sq_m: Number(areaSqM.toFixed(2)),
          area_sq_ft: Number((areaSqM * SQ_FT_PER_SQ_M).toFixed(1)),
          centroid,
          was_reversed: wasReversed,
        });
      }
    }
  }

  const closedCoords = [...snapped.map(([x, y]) => [x, y]), [snapped[0][0], snapped[0][1]]];
  return {
    ok: true,
    geometry: { type: "Polygon", coordinates: [closedCoords] },
    vertex_count: n,
    area_sq_m: Number(areaSqM.toFixed(2)),
    area_sq_ft: Number((areaSqM * SQ_FT_PER_SQ_M).toFixed(1)),
    centroid,
    was_reversed: wasReversed,
    errors: [],
  };
}

/**
 * Balanced JSON scanner matching `schema._find_tagged_json_spans` in Python (DF-2, DF-4).
 * Properly walks JSON string escapes and nested braces/brackets (`]]]` inside Polygon coords).
 */
function findTaggedJsonSpansJs(text, prefix) {
  const s = String(text || "");
  if (!s || !prefix || !s.includes(prefix)) return [];
  const spans = [];
  let pos = 0;
  const n = s.length;
  const pLen = prefix.length;

  while (pos < n) {
    const idx = s.indexOf(prefix, pos);
    if (idx < 0) break;
    let cur = idx + pLen;
    while (cur < n && /\s/.test(s[cur])) cur++;
    if (cur >= n || (s[cur] !== "{" && s[cur] !== "[")) {
      pos = idx + pLen;
      continue;
    }

    const startJson = cur;
    const stack = [s[cur]];
    let inString = false;
    let escaped = false;
    let endJson = -1;

    for (let i = cur + 1; i < n; i++) {
      const ch = s[i];
      if (inString) {
        if (escaped) {
          escaped = false;
        } else if (ch === "\\") {
          escaped = true;
        } else if (ch === '"') {
          inString = false;
        }
        continue;
      }
      if (ch === '"') {
        inString = true;
      } else if (ch === "{" || ch === "[") {
        stack.push(ch);
      } else if (ch === "}" || ch === "]") {
        const top = stack[stack.length - 1];
        if ((ch === "}" && top === "{") || (ch === "]" && top === "[")) {
          stack.pop();
          if (stack.length === 0) {
            endJson = i + 1;
            break;
          }
        } else {
          break;
        }
      }
    }

    if (endJson < 0) {
      pos = idx + pLen;
      continue;
    }
    let closeIdx = endJson;
    while (closeIdx < n && /\s/.test(s[closeIdx])) closeIdx++;
    if (closeIdx >= n || s[closeIdx] !== "]") {
      pos = idx + pLen;
      continue;
    }
    try {
      const parsed = JSON.parse(s.slice(startJson, endJson));
      spans.push({ start: idx, end: closeIdx + 1, value: parsed });
      pos = closeIdx + 1;
    } catch (_e) {
      pos = idx + pLen;
    }
  }
  return spans;
}

function stripTaggedJsonSpansJs(text, prefix) {
  const s = String(text || "");
  const spans = findTaggedJsonSpansJs(s, prefix);
  if (spans.length === 0) return s.trim();
  const parts = [];
  let last = 0;
  for (const span of spans) {
    parts.push(s.slice(last, span.start));
    last = span.end;
  }
  parts.push(s.slice(last));
  return parts
    .join("")
    .replace(/[ \t]{2,}/g, " ")
    .replace(/\s*\|\s*$/, "")
    .replace(/^\s*\|\s*/, "")
    .replace(/\s*\|\s*\|\s*/g, " | ")
    .trim();
}

export function extractEmbeddedGeometryJson(sourceCitation) {
  const spans = findTaggedJsonSpansJs(String(sourceCitation || ""), GEOMETRY_TAG_PREFIX);
  for (let i = spans.length - 1; i >= 0; i--) {
    const res = validateOverridePolygonGeometry(spans[i].value);
    if (res.ok) return res.geometry;
  }
  return null;
}

export function stripEmbeddedGeometryTag(sourceCitation) {
  return stripTaggedJsonSpansJs(String(sourceCitation || ""), GEOMETRY_TAG_PREFIX);
}

export function encodeCitationWithGeometry(sourceCitation, geometryOrRing) {
  const base = stripEmbeddedGeometryTag(sanitizeAtlasTextJs(sourceCitation));
  const res = validateOverridePolygonGeometry(geometryOrRing);
  if (!res.ok) {
    throw new Error(res.errors.join("; "));
  }
  const compactGeom = JSON.stringify(res.geometry);
  if (compactGeom.length > MAX_GEOMETRY_JSON_CELL_CHARS) {
    throw new Error(
      `Polygon GeoJSON exceeds ${MAX_GEOMETRY_JSON_CELL_CHARS}-character Google Sheets safety limit (${compactGeom.length} chars).`
    );
  }
  const tag = `${GEOMETRY_TAG_PREFIX}${compactGeom}]`;
  const combined = base ? `${base} ${tag}` : tag;
  if (combined.length > GOOGLE_SHEETS_CELL_MAX_CHARS) {
    throw new Error(
      `Encoded citation exceeds Google Sheets ${GOOGLE_SHEETS_CELL_MAX_CHARS}-character cell limit (got ${combined.length} chars).`
    );
  }
  return combined;
}

export function extractEmbeddedFieldSurveyJson(sourceCitation) {
  const spans = findTaggedJsonSpansJs(String(sourceCitation || ""), FIELD_SURVEY_TAG_PREFIX);
  for (let i = spans.length - 1; i >= 0; i--) {
    const val = spans[i].value;
    if (val && typeof val === "object" && !Array.isArray(val)) {
      return val;
    }
  }
  return null;
}

export const extractFieldSurveyEnvelope = extractEmbeddedFieldSurveyJson;

export function stripEmbeddedFieldSurveyTag(sourceCitation) {
  return stripTaggedJsonSpansJs(String(sourceCitation || ""), FIELD_SURVEY_TAG_PREFIX);
}

export function encodeFieldSurveyEnvelope(sourceCitation, envelope = {}) {
  const base = stripEmbeddedFieldSurveyTag(sanitizeAtlasTextJs(sourceCitation));
  const cleanEnv = sanitizePayloadStringsJs({ ...(envelope || {}) });
  const compactEnv = JSON.stringify(cleanEnv);
  const tag = `${FIELD_SURVEY_TAG_PREFIX}${compactEnv}]`;
  const combined = base ? `${base} ${tag}` : tag;
  if (combined.length > GOOGLE_SHEETS_CELL_MAX_CHARS) {
    throw new Error(
      `Encoded field survey citation exceeds Google Sheets ${GOOGLE_SHEETS_CELL_MAX_CHARS}-character limit (got ${combined.length} chars).`
    );
  }
  return combined;
}

export function extractGeometryAndCleanCitation(rowOrCitation) {
  let rawGeomCell = "";
  let rawCitation = "";
  if (typeof rowOrCitation === "string") {
    rawCitation = rowOrCitation;
  } else if (rowOrCitation && typeof rowOrCitation === "object") {
    if (rowOrCitation.geometry_geojson && typeof rowOrCitation.geometry_geojson === "object") {
      rawGeomCell = JSON.stringify(rowOrCitation.geometry_geojson);
    } else {
      rawGeomCell = String(rowOrCitation.geometry_geojson || "").trim();
    }
    rawCitation = String(
      rowOrCitation.source_citation || rowOrCitation.directory_evidence || rowOrCitation.notes || ""
    );
  }
  let geometry = null;
  if (rawGeomCell) {
    try {
      const parsed = JSON.parse(rawGeomCell);
      const res = validateOverridePolygonGeometry(parsed);
      if (res.ok) geometry = res.geometry;
    } catch (_e) {
      geometry = null;
    }
  }
  if (!geometry && rawCitation) {
    geometry = extractEmbeddedGeometryJson(rawCitation);
  }
  const cleanCitation = stripEmbeddedFieldSurveyTag(stripEmbeddedGeometryTag(rawCitation));
  return { geometry, cleanCitation };
}

function cleanHcadPrefixFromKey(raw) {
  const prefix = String(raw || "").split("#")[0].trim();
  return /^\d+$/.test(prefix) ? prefix : "";
}

/**
 * Canonical suppression predicate enforcing consistent OR-precedence:
 * returns true if any matching sub-building / feature ID is `suppress_only`,
 * OR if a whole-parcel override (`!is_building_override`) for the 13-digit
 * `hcad_num` is `suppress_only`.
 */
export function isEntitySuppressedByOverrides(
  overridesMap,
  { buildingId = "", featureId = "", hcadNum = "" } = {}
) {
  const ovMap = overridesMap?.overrides || overridesMap;
  if (!ovMap || typeof ovMap !== "object") return false;
  const cleanBid = String(buildingId || "").trim();
  const cleanFid = String(featureId || "").trim();
  const inferredHcad =
    cleanHcadPrefixFromKey(hcadNum) ||
    cleanHcadPrefixFromKey(cleanBid) ||
    cleanHcadPrefixFromKey(cleanFid);

  if (cleanBid && ovMap[cleanBid]?.suppress_only) return true;
  if (cleanFid && ovMap[cleanFid]?.suppress_only) return true;
  if (
    inferredHcad &&
    ovMap[inferredHcad] &&
    !ovMap[inferredHcad].is_building_override &&
    ovMap[inferredHcad].suppress_only
  ) {
    return true;
  }
  return false;
}

/**
 * Return true when an override row or submission payload marks a demolished structure
 * whose lot is currently vacant (hiding the ghost building footprint while leaving
 * the parcel clickable as a vacant lot).
 */
export function isDemolishedVacantOverrideInput({
  yearRaw = "",
  footprintIssue = "",
  footprintNotes = "",
  sourceType = "",
  sourceCitation = "",
  suppressOnly = null,
} = {}) {
  if (suppressOnly === true) return true;

  const cleanCitation = stripEmbeddedGeometryTag(sourceCitation);
  const rawStr = yearRaw === null || yearRaw === undefined ? "" : String(yearRaw).trim();
  const lowYr = rawStr.toLowerCase();
  if (EXPLICIT_VACANT_YEAR_TOKENS.has(lowYr)) {
    if (suppressOnly === false && lowYr === "0") return false;
    return true;
  }

  if (suppressOnly === false) {
    return false;
  }

  let parsedYr = 0;
  if (rawStr !== "") {
    const num = Number(rawStr);
    parsedYr = Number.isFinite(num) ? Math.round(num) : NaN;
  }
  if (
    Number.isFinite(parsedYr) &&
    parsedYr >= MIN_VALID_YEAR &&
    parsedYr <= MAX_STANDING_OVERRIDE_YEAR
  ) {
    return false;
  }
  if (parsedYr !== 0) {
    return false;
  }

  const combinedMeta = [footprintIssue, sourceType, cleanCitation].filter(Boolean).join(" | ");
  if (RE_DEMOLISHED_VACANT_TEXT.test(combinedMeta)) {
    return true;
  }

  const issueAndSrc = `${footprintIssue} | ${sourceType}`;
  const notesAndCite = `${footprintNotes} | ${cleanCitation}`;
  if (RE_LEGACY_DEMOLISHED_ISSUE.test(issueAndSrc) && RE_VACANT_LOT_CUE.test(notesAndCite)) {
    return true;
  }

  return false;
}

/**
 * Convert a standard Google Sheets browser URL (`.../spreadsheets/d/<ID>/edit...`)
 * into a direct CORS-friendly CSV export URL if the user pasted an edit link.
 */
export function normalizeGoogleSheetCsvUrl(rawUrl) {
  const trimmed = String(rawUrl || "").trim();
  if (!trimmed) return "";

  // Already a published CSV or gviz CSV export URL
  if (trimmed.includes("output=csv") || trimmed.includes("tqx=out:csv")) {
    return trimmed;
  }

  // Standard Google Sheet URL: https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit...
  const match = trimmed.match(/\/spreadsheets\/d\/([a-zA-Z0-9-_]+)/);
  if (match && match[1] && match[1] !== "e") {
    const sheetId = match[1];
    const gidMatch = trimmed.match(/[?#&]gid=(\d+)/);
    if (gidMatch) {
      return `https://docs.google.com/spreadsheets/d/${sheetId}/export?format=csv&gid=${gidMatch[1]}`;
    }
    return `https://docs.google.com/spreadsheets/d/${sheetId}/gviz/tq?tqx=out:csv&sheet=Approved_Edits`;
  }

  return trimmed;
}

/**
 * Compute normalized atlas decade (1840 for 1836-1849, otherwise floor(year/10)*10).
 */
export function computeNormalizedDecade(yearBuilt) {
  const yr = Number(yearBuilt) || 0;
  if (yr < MIN_VALID_YEAR || yr > MAX_STANDING_OVERRIDE_YEAR) return 0;
  if (yr < 1850) return 1840;
  return Math.floor(yr / 10) * 10;
}

/**
 * RFC 4180 compliant CSV parser that returns an array of row objects keyed by lowercase header name.
 */
export function parseCsvToObjects(csvText) {
  const text = String(csvText || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const rows = [];
  let currentRow = [];
  let currentField = "";
  let inQuotes = false;

  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"') {
        if (i + 1 < text.length && text[i + 1] === '"') {
          currentField += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        currentField += ch;
      }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === ",") {
      currentRow.push(currentField.trim());
      currentField = "";
    } else if (ch === "\n") {
      currentRow.push(currentField.trim());
      if (currentRow.some((cell) => cell !== "")) {
        rows.push(currentRow);
      }
      currentRow = [];
      currentField = "";
    } else {
      currentField += ch;
    }
  }
  currentRow.push(currentField.trim());
  if (currentRow.some((cell) => cell !== "")) {
    rows.push(currentRow);
  }

  if (rows.length < 2) return [];
  const headers = rows[0].map((h) =>
    h
      .toLowerCase()
      .replace(/[^a-z0-9_]+/g, "_")
      .replace(/^_|_$/g, "")
  );

  const objects = [];
  for (let r = 1; r < rows.length; r++) {
    const row = rows[r];
    const obj = {};
    for (let c = 0; c < headers.length; c++) {
      if (headers[c]) {
        obj[headers[c]] = row[c] !== undefined ? row[c] : "";
      }
    }
    objects.push(obj);
  }
  return objects;
}

/**
 * Convert parsed Google Sheet CSV rows into normalized override records keyed by 13-digit `hcad_num`.
 * Only rows with `status` empty or in `["approved", "verified", "live"]` are accepted.
 */
export function parseOverridesFromSheetRows(rows) {
  const overrides = {};
  for (const row of Array.isArray(rows) ? rows : []) {
    const status = String(row.status || row.review_status || "approved")
      .trim()
      .toLowerCase();
    if (status && !["approved", "verified", "live", "active", "yes"].includes(status)) {
      continue;
    }

    const rawSourceTypeCheck = String(row.source_type || row.evidence_source || "").trim().toUpperCase();
    if (
      rawSourceTypeCheck === "PLAT_MONITOR_REVIEW" ||
      rawSourceTypeCheck === "PROPERTY_WATCHLIST_UPDATE" ||
      rawSourceTypeCheck === FIELD_SURVEY_SOURCE_TYPE ||
      rawSourceTypeCheck === "BUILDING_FLAG" ||
      rawSourceTypeCheck === "DATA_FLAG" ||
      rawSourceTypeCheck.endsWith("_FLAG") ||
      FLAG_SOURCE_TYPES.has(rawSourceTypeCheck)
    ) {
      continue;
    }

    const rawCitationField = String(
      row.source_citation || row.directory_evidence || row.notes || ""
    ).trim();
    if (rawCitationField.includes(FIELD_SURVEY_TAG_PREFIX)) {
      continue;
    }

    const rawIdField = String(
      row.building_id || row.hcad_num || row.hcad_account || row.account || row.acct || ""
    ).trim();
    if (
      rawIdField.startsWith("PLAT_") ||
      rawIdField.startsWith("WATCH_") ||
      rawIdField.startsWith("FLAG_") ||
      rawIdField.startsWith("NARRATIVE_") ||
      rawIdField.startsWith("SURV_")
    ) {
      continue;
    }
    const rawHcad = rawIdField.split("#")[0].replace(/\D/g, "");
    if (!rawHcad) continue;
    const hcadNum = rawHcad.length < 13 ? rawHcad.padStart(13, "0") : rawHcad;
    const rawSuffix = rawIdField.includes("#")
      ? rawIdField
          .split("#")
          .slice(1)
          .join("#")
          .replace(/[^A-Za-z0-9_#-]/g, "")
      : "";
    const hasBuildingSuffix = Boolean(rawSuffix);
    const overrideKey = hasBuildingSuffix ? `${hcadNum}#${rawSuffix}` : hcadNum;

    const { geometry: resolvedGeom, cleanCitation } = extractGeometryAndCleanCitation(row);

    let rawYearField = "";
    for (const yrCol of ["year_built", "verified_year_built", "suggested_year_built", "corrected_year"]) {
      if (row[yrCol] !== undefined && row[yrCol] !== null && String(row[yrCol]).trim() !== "") {
        rawYearField = String(row[yrCol]).trim();
        break;
      }
    }
    const rawSuppressCell = String(row.suppress_only ?? "").trim().toLowerCase();
    const explicitSuppressOnly =
      row.suppress_only === true || ["true", "1", "yes"].includes(rawSuppressCell)
        ? true
        : row.suppress_only === false || ["false", "0", "no"].includes(rawSuppressCell)
        ? false
        : null;
    const isSuppressOnly = isDemolishedVacantOverrideInput({
      yearRaw: rawYearField,
      footprintIssue: String(row.footprint_issue || "").trim(),
      footprintNotes: String(row.footprint_notes || row.notes || "").trim(),
      sourceType: String(row.source_type || row.evidence_source || "").trim(),
      sourceCitation: cleanCitation,
      suppressOnly: explicitSuppressOnly,
      hasGeometry: Boolean(resolvedGeom),
    });

    const yrRaw = rawYearField !== "" ? Number(rawYearField) : 0;
    const yearBuilt = isSuppressOnly
      ? 0
      : Number.isFinite(yrRaw) &&
        yrRaw >= MIN_VALID_YEAR &&
        yrRaw <= MAX_STANDING_OVERRIDE_YEAR
      ? Math.round(yrRaw)
      : 0;
    const origYrRaw = Number(
      row.original_hcad_year || row.hcad_year_built || row.current_year_built || 0
    );

    const geometry = isSuppressOnly ? null : resolvedGeom;

    const rawAltNames = String(
      row.alt_names || row.alternate_names || row.aka || row.historical_names || ""
    ).trim();
    const parsedAltNames = rawAltNames
      ? rawAltNames
          .split(/[;|]/)
          .map((s) => s.trim())
          .filter(Boolean)
      : [];

    let rawAddress = String(row.address || row.street_address || "").trim();
    let rawBuildingName = String(
      row.building_name || row.landmark_name || row.historic_name || ""
    ).trim();
    let rawLandmarkName = String(
      row.landmark_name || row.historic_name || row.building_name || ""
    ).trim();
    let rawStyle = String(row.bld_style || row.style || "").trim();

    // Unpack legacy "<Landmark Name> (<Street Address>)" strings in the sheet's address column
    const combinedAddrMatch = rawAddress.match(/^(.+?)\s*\((\d+\s+[^()]+)\)$/);
    if (combinedAddrMatch) {
      const extractedName = combinedAddrMatch[1].trim();
      const extractedStreet = combinedAddrMatch[2].trim();
      rawAddress = extractedStreet;
      if (!rawBuildingName) rawBuildingName = extractedName;
      if (!rawLandmarkName) rawLandmarkName = extractedName;
    }

    if (!rawStyle && cleanCitation) {
      const styleMatch = cleanCitation.match(/(?:^|\.\s*|\s+)Style:\s*([^.|;]+?)(?:\.|$)/i);
      if (styleMatch) {
        rawStyle = styleMatch[1].trim();
      }
    }

    const rawReplaceCell = String(row.replace_parcel_shards ?? "").trim().toLowerCase();
    const explicitReplace =
      row.replace_parcel_shards === true || ["true", "1", "yes"].includes(rawReplaceCell)
        ? true
        : row.replace_parcel_shards === false || ["false", "0", "no"].includes(rawReplaceCell)
        ? false
        : null;

    const incomingRec = {
      id: overrideKey,
      building_id: overrideKey,
      hcad_num: hcadNum,
      is_building_override: hasBuildingSuffix,
      suppress_only: Boolean(isSuppressOnly),
      address: rawAddress.toUpperCase(),
      historic_district: String(row.historic_district || row.district || "").trim(),
      contributing: String(row.contributing || row.contributing_status || "").trim(),
      year_built: yearBuilt,
      decade: isSuppressOnly ? 0 : computeNormalizedDecade(yearBuilt),
      original_hcad_year: origYrRaw >= 1830 ? Math.round(origYrRaw) : 0,
      bld_style: rawStyle,
      architect: String(row.architect || "").trim(),
      builder: String(row.builder || row.contractor || row.general_contractor || "").trim(),
      builder_source: String(row.builder_source || "").trim(),
      engineers: String(row.engineers || "").trim(),
      original_permit: String(row.original_permit || "").trim(),
      original_cost: String(row.original_cost || "").trim(),
      yr_remodel: Number(row.yr_remodel) || 0,
      eff_year_built: Number(row.eff_year_built) || 0,
      was_relocated: Boolean(row.was_relocated),
      landmark_name: rawLandmarkName,
      building_name: rawBuildingName,
      ...(parsedAltNames.length ? { alt_names: parsedAltNames } : {}),
      landmark_type: String(row.landmark_type || "").trim(),
      source_type: String(
        row.source_type ||
          row.evidence_source ||
          (geometry && !yearBuilt
            ? "Satellite / Aerial Imagery"
            : "Preservation Houston Archival Record")
      ).trim(),
      source_citation: cleanCitation,
      source_url: String(row.source_url || row.contentdm_url || "").trim(),
      verified_by: String(row.verified_by || "Preservation Houston").trim(),
      updated_at: String(row.updated_at || "").trim(),
      geometry,
      ...(explicitReplace !== null ? { replace_parcel_shards: explicitReplace } : {}),
      ...(hasBuildingSuffix
        ? {
            keep_shard_footprints: isSuppressOnly || Boolean(geometry) ? false : true,
            ...(explicitReplace === null ? { replace_parcel_shards: false } : {}),
          }
        : {}),
    };

    overrides[overrideKey] = Object.prototype.hasOwnProperty.call(overrides, overrideKey)
      ? mergeOverrideRecord(overrides[overrideKey], incomingRec, { overrideKey })
      : mergeOverrideRecord({}, incomingRec, { overrideKey });
  }
  return overrides;
}

/**
 * Parse approved flag/rejection rows (`PHOTO_MISATTRIBUTION_FLAG`, `CLAIM_DISPUTE_FLAG`,
 * `NARRATIVE_ERROR_FLAG`) from the published `Approved_Edits` CSV so that once a
 * Preservation Houston administrator marks a flag row `Approved`, the rejected
 * automated photo or unverified claim is suppressed globally for all visitors.
 */
export function parseGlobalRejectionsFromSheetRows(rows) {
  const rejectedPhotos = [];
  const rejectedClaims = [];
  const flaggedNarratives = [];

  for (const row of Array.isArray(rows) ? rows : []) {
    const status = String(row.status || row.review_status || "approved")
      .trim()
      .toLowerCase();
    if (status && !["approved", "verified", "live", "active", "yes"].includes(status)) {
      continue;
    }
    const sourceType = String(row.source_type || row.evidence_source || "")
      .trim()
      .toUpperCase();
    if (!FLAG_SOURCE_TYPES.has(sourceType)) continue;

    const rawIdField = String(
      row.building_id || row.hcad_num || row.hcad_account || row.account || ""
    ).trim();
    const rawHcadDigits = rawIdField.split("#")[0].replace(/\D/g, "");
    const hcadNum =
      rawHcadDigits.length >= 10 && rawHcadDigits.length <= 13
        ? rawHcadDigits.padStart(13, "0")
        : "";
    const citation = String(row.source_citation || row.notes || "").trim();

    if (sourceType === "PHOTO_MISATTRIBUTION_FLAG") {
      const photoUrl = String(row.photo_url || row.source_url || "").trim();
      if (photoUrl) {
        rejectedPhotos.push({
          hcadNum,
          photoUrl,
          citation,
        });
      }
    } else if (sourceType === "CLAIM_DISPUTE_FLAG") {
      const fieldMatch = citation.match(/\[DISPUTED FIELD:\s*([^\]]+)\]/i);
      const valMatch = citation.match(/\[DISPUTED VALUE:\s*([^\]]+)\]/i);
      const field = fieldMatch ? fieldMatch[1].trim().toLowerCase() : "building_name";
      const disputedValue = valMatch
        ? valMatch[1].trim()
        : String(row.building_name || row.architect || "").trim();
      if (hcadNum || disputedValue) {
        rejectedClaims.push({
          hcadNum,
          field,
          disputedValue,
          citation,
        });
      }
    } else if (sourceType === "NARRATIVE_ERROR_FLAG") {
      flaggedNarratives.push({
        contextId: rawIdField || String(row.address || "").trim(),
        citation,
      });
    }
  }

  return {
    rejectedPhotos,
    rejectedClaims,
    flaggedNarratives,
  };
}

/**
 * Classify a displayed property attribute (`building_name`, `year_built`, `architect`, `style`)
 * into the Atlas 4-Tier Evidentiary Provenance model:
 *   Tier 1: Verified Primary Record (COH Landmark Report, Good Brick, NRHP, THC, City Directory, Sanborn, Deed/Permit)
 *   Tier 2: Published Survey (PH Curated Archive, AIA Guide, HMRC Contractor Archive, 1980 Historic Survey)
 *   Tier 3: HCAD Appraisal Roll / Rounded Decade Estimate
 *   Tier 4: Unverified Automated Match (HAIF forum scrape, OpenStreetMap/Wikidata unverified tag)
 */
export function classifyFieldEvidentiaryTier({
  field = "",
  value = "",
  sourceLabel = "",
  isCuratedOverride = false,
  isRoundedHcadDecade = false,
  isCityDirContradicted = false,
} = {}) {
  const src = String(sourceLabel || "").trim();
  const srcLower = src.toLowerCase();

  const isTier4Auto =
    srcLower.includes("haif") ||
    srcLower.includes("houston architecture forum") ||
    srcLower.includes("houston architecture info forum") ||
    srcLower.includes("openstreetmap") ||
    srcLower.includes("wikidata") ||
    srcLower.includes("automated");

  if (isTier4Auto) {
    return {
      tier: 4,
      tierLabel: "Tier 4 · Unverified Automated Match",
      shortBadge: srcLower.includes("haif") || srcLower.includes("forum")
        ? "Unverified Automated Match · HAIF"
        : "Unverified Automated Match",
      requiresFlagBadge: true,
      isContradicted: false,
    };
  }

  const isTier1Primary =
    srcLower.includes("landmark") ||
    srcLower.includes("good brick") ||
    srcLower.includes("city directory") ||
    srcLower.includes("morrison") ||
    srcLower.includes("sanborn") ||
    srcLower.includes("national register") ||
    srcLower.includes("nrhp") ||
    srcLower.includes("texas historical commission") ||
    srcLower.includes("thc") ||
    srcLower.includes("habs") ||
    srcLower.includes("building permit") ||
    srcLower.includes("deed") ||
    srcLower.includes("plat") ||
    srcLower.includes("archival review");

  if (isTier1Primary) {
    return {
      tier: 1,
      tierLabel: "Tier 1 · Verified Primary Record",
      shortBadge: "Verified Primary Record",
      requiresFlagBadge: false,
      isContradicted: false,
    };
  }

  if (
    isCuratedOverride ||
    srcLower.includes("preservation houston") ||
    srcLower.includes("hmrc") ||
    srcLower.includes("aia") ||
    srcLower.includes("survey") ||
    srcLower.includes("t.g.c.a")
  ) {
    return {
      tier: 2,
      tierLabel: "Tier 2 · Published Architectural Survey",
      shortBadge: "Published Survey",
      requiresFlagBadge: false,
      isContradicted: false,
    };
  }

  if (field === "year_built" && isRoundedHcadDecade) {
    return {
      tier: 3,
      tierLabel: "Tier 3 · HCAD Rounded Decade Estimate",
      shortBadge: isCityDirContradicted ? "HCAD Est. · Contradicted by 1926 Dir." : "HCAD Est.",
      requiresFlagBadge: false,
      isContradicted: Boolean(isCityDirContradicted),
    };
  }

  return {
    tier: 3,
    tierLabel: "Tier 3 · HCAD Appraisal Roll",
    shortBadge: isCityDirContradicted ? "HCAD Roll · Contradicted by 1926 Dir." : "HCAD Roll",
    requiresFlagBadge: false,
    isContradicted: Boolean(isCityDirContradicted),
  };
}

export function getLocallyHiddenClaims() {
  if (typeof localStorage === "undefined") return [];
  try {
    const raw = localStorage.getItem(LS_HIDDEN_CLAIMS_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch (_e) {
    return [];
  }
}

export function hideDisputedClaimLocally({ hcadNum = "", field = "", value = "" } = {}) {
  if (typeof localStorage === "undefined") return;
  const cleanHcad = String(hcadNum || "").trim();
  const cleanField = String(field || "").trim().toLowerCase();
  const cleanValue = String(value || "").trim().toLowerCase();
  if (!cleanField || (!cleanHcad && !cleanValue)) return;

  const list = getLocallyHiddenClaims();
  const exists = list.some(
    (item) =>
      item.hcadNum === cleanHcad &&
      item.field === cleanField &&
      item.value === cleanValue
  );
  if (!exists) {
    list.unshift({ hcadNum: cleanHcad, field: cleanField, value: cleanValue });
    try {
      localStorage.setItem(LS_HIDDEN_CLAIMS_KEY, JSON.stringify(list.slice(0, 300)));
    } catch (_e) {
      // Ignore storage quota warnings
    }
  }
}

export function isClaimSuppressed(
  { hcadNum = "", field = "", value = "" } = {},
  globalRejections = null
) {
  const cleanHcad = String(hcadNum || "").split("#")[0].trim();
  const cleanField = String(field || "").trim().toLowerCase();
  const cleanValue = String(value || "").trim().toLowerCase();
  if (!cleanValue) return false;

  for (const item of getLocallyHiddenClaims()) {
    if (
      item.field === cleanField &&
      item.value === cleanValue &&
      (!item.hcadNum || !cleanHcad || item.hcadNum === cleanHcad)
    ) {
      return true;
    }
  }

  const globalList = globalRejections?.rejectedClaims;
  if (Array.isArray(globalList)) {
    for (const item of globalList) {
      const itemField = String(item.field || "").trim().toLowerCase();
      const itemVal = String(item.disputedValue || "").trim().toLowerCase();
      const itemHcad = String(item.hcadNum || "").trim();
      if (
        (!itemField || itemField === cleanField) &&
        (!itemVal || itemVal === cleanValue) &&
        (!itemHcad || !cleanHcad || itemHcad === cleanHcad)
      ) {
        return true;
      }
    }
  }
  return false;
}

export function isObsoleteRestrictedSheetUrl(url) {
  const s = String(url || "").trim();
  if (!s) return false;
  // Once a Google Sheet is set to Restricted, direct /gviz/tq or /export URLs on the raw sheet ID
  // redirect to accounts.google.com/ServiceLogin and fail CORS; only /spreadsheets/d/e/2PACX-.../pub works.
  if (s.includes("/gviz/tq") || s.includes("1dpd9A5z4SmFkHut-fmBi8deIDGVZ8bDzYVdP88-_ZDs")) {
    return true;
  }
  return false;
}

/**
 * Load baseline overrides from `public/data/curated_overrides.json` and optionally merge
 * live approved rows from a published Google Sheet CSV URL (with automatic fallback to
 * the bound Google Apps Script `doGet` CSV endpoint).
 */
export async function loadCuratedOverrides(customSheetCsvUrl = null) {
  let baseConfig = {
    googleSheetCsvUrl: "",
    submissionWebhookUrl: "",
    overrides: {},
  };

  try {
    const res = await fetch("public/data/curated_overrides.json?v=20261030e", { cache: "no-store" });
    if (res.ok) {
      const data = await res.json();
      baseConfig = {
        googleSheetCsvUrl: data.googleSheetCsvUrl || "",
        submissionWebhookUrl: data.submissionWebhookUrl || "",
        overrides: data.overrides || {},
      };
    }
  } catch (err) {
    console.warn("Could not load baseline curated_overrides.json:", err);
  }

  let storedCsvUrl =
    typeof localStorage !== "undefined" ? localStorage.getItem(LS_SHEET_CSV_URL_KEY) : "";
  if (storedCsvUrl && isObsoleteRestrictedSheetUrl(storedCsvUrl)) {
    if (typeof localStorage !== "undefined") {
      localStorage.removeItem(LS_SHEET_CSV_URL_KEY);
    }
    storedCsvUrl = "";
  }

  const storedWebhookUrl =
    typeof localStorage !== "undefined" ? localStorage.getItem(LS_WEBHOOK_URL_KEY) : "";

  const candidateCsvUrl =
    customSheetCsvUrl !== null && !isObsoleteRestrictedSheetUrl(customSheetCsvUrl)
      ? customSheetCsvUrl
      : storedCsvUrl || baseConfig.googleSheetCsvUrl;

  const activeSheetCsvUrl = normalizeGoogleSheetCsvUrl(candidateCsvUrl);
  const activeWebhookUrl = (storedWebhookUrl || baseConfig.submissionWebhookUrl || "").trim();

  const mergedOverrides = { ...baseConfig.overrides };
  let mergedRejections = {
    rejectedPhotos: [],
    rejectedClaims: [],
    flaggedNarratives: [],
  };
  let sheetSyncStatus = {
    connected: Boolean(activeSheetCsvUrl || activeWebhookUrl),
    csvUrl: activeSheetCsvUrl,
    webhookUrl: activeWebhookUrl,
    sheetRowCount: 0,
    totalOverrideCount: Object.keys(mergedOverrides).length,
    error: null,
  };

  const syncedHcads = new Set();
  const applyCsvText = (csvText) => {
    const rows = parseCsvToObjects(csvText);
    const parsedRejections = parseGlobalRejectionsFromSheetRows(rows);
    mergedRejections = {
      rejectedPhotos: [...mergedRejections.rejectedPhotos, ...parsedRejections.rejectedPhotos],
      rejectedClaims: [...mergedRejections.rejectedClaims, ...parsedRejections.rejectedClaims],
      flaggedNarratives: [...mergedRejections.flaggedNarratives, ...parsedRejections.flaggedNarratives],
    };
    const sheetOverrides = parseOverridesFromSheetRows(rows);
    for (const [hcadNum, ov] of Object.entries(sheetOverrides)) {
      syncedHcads.add(hcadNum);
      mergedOverrides[hcadNum] = mergeOverrideRecord(mergedOverrides[hcadNum] || {}, ov, {
        overrideKey: hcadNum,
      });
    }
    sheetSyncStatus.sheetRowCount = syncedHcads.size;
    sheetSyncStatus.totalOverrideCount = Object.keys(mergedOverrides).length;
  };

  // Fetch the published Google Sheet CSV (or fall back to the Apps Script doGet endpoint if no CSV URL is set).
  // Avoiding a redundant GET against the POST-only Apps Script webhook prevents a 404 console warning from script.googleusercontent.com.
  const fetchPromises = [];
  if (activeSheetCsvUrl) {
    fetchPromises.push(
      fetch(activeSheetCsvUrl, { cache: "no-store" }).then(async (r) => {
        if (!r.ok) throw new Error(`CSV HTTP ${r.status}`);
        return { source: "csv", text: await r.text() };
      })
    );
  } else if (activeWebhookUrl) {
    fetchPromises.push(
      fetch(activeWebhookUrl, { cache: "no-store" }).then(async (r) => {
        if (!r.ok) throw new Error(`Webhook HTTP ${r.status}`);
        return { source: "webhook", text: await r.text() };
      })
    );
  }

  if (fetchPromises.length > 0) {
    const settled = await Promise.allSettled(fetchPromises);
    let anySucceeded = false;
    let firstError = null;
    for (const item of settled) {
      if (item.status === "fulfilled" && item.value && item.value.text) {
        applyCsvText(item.value.text);
        anySucceeded = true;
      } else if (item.status === "rejected" && !firstError) {
        firstError = item.reason;
      }
    }
    if (!anySucceeded && firstError) {
      sheetSyncStatus.error = firstError.message || String(firstError);
      console.warn("Google Sheet live sync failed (using baseline overrides):", firstError);
    }
  }

  return {
    overrides: mergedOverrides,
    globalRejections: mergedRejections,
    syncStatus: sheetSyncStatus,
  };
}

/**
 * Save or clear custom Google Sheet CSV & Apps Script Webhook URLs in browser storage.
 */
export function saveGoogleSheetEndpoints({ csvUrl, webhookUrl }) {
  if (typeof localStorage === "undefined") return;
  const normalizedCsv = normalizeGoogleSheetCsvUrl(csvUrl);
  if (normalizedCsv && !isObsoleteRestrictedSheetUrl(normalizedCsv)) {
    localStorage.setItem(LS_SHEET_CSV_URL_KEY, normalizedCsv);
  } else {
    localStorage.removeItem(LS_SHEET_CSV_URL_KEY);
  }

  const trimmedWebhook = String(webhookUrl || "").trim();
  if (trimmedWebhook) {
    localStorage.setItem(LS_WEBHOOK_URL_KEY, trimmedWebhook);
  } else {
    localStorage.removeItem(LS_WEBHOOK_URL_KEY);
  }
}

/**
 * Merge an incoming sheet or Staff Admin override record onto an existing override record,
 * handling `suppress_only` footprint clearing, `_pre_suppress_geometry` stashing,
 * and replacement-structure revival (`>= MIN_VALID_YEAR`).
 */
export function mergeOverrideRecord(
  existingOv = {},
  incomingOv = {},
  { overrideKey = "", fallbackGeometry = null } = {}
) {
  const key = String(
    overrideKey ||
      incomingOv.building_id ||
      incomingOv.id ||
      existingOv.building_id ||
      existingOv.id ||
      incomingOv.hcad_num ||
      ""
  ).trim();
  const isSub =
    key.includes("#") ||
    Boolean(incomingOv.is_building_override || existingOv.is_building_override);
  const wasSuppressed = Boolean(existingOv.suppress_only);
  const priorYrRaw = Number(existingOv.year_built) || 0;
  const priorYr =
    priorYrRaw >= MIN_VALID_YEAR && priorYrRaw <= MAX_STANDING_OVERRIDE_YEAR
      ? Math.round(priorYrRaw)
      : 0;
  const incomingYrRaw = Number(incomingOv.year_built) || 0;
  const incomingYr =
    incomingYrRaw >= MIN_VALID_YEAR && incomingYrRaw <= MAX_STANDING_OVERRIDE_YEAR
      ? Math.round(incomingYrRaw)
      : 0;
  const incomingGeom =
    incomingOv.geometry &&
    typeof incomingOv.geometry === "object" &&
    (incomingOv.geometry.type === "Polygon" || incomingOv.geometry.type === "MultiPolygon")
      ? incomingOv.geometry
      : null;
  const isGeomOnly = Boolean(incomingGeom && incomingYr === 0 && !incomingOv.suppress_only);

  const merged = { ...existingOv };
  for (const [k, v] of Object.entries(incomingOv)) {
    if (
      k === "geometry" ||
      k === "year_built" ||
      k === "decade" ||
      k === "keep_shard_footprints" ||
      k === "replace_parcel_shards" ||
      k === "suppress_only"
    ) {
      continue;
    }
    if (k === "source_type" && isGeomOnly && priorYr >= MIN_VALID_YEAR && !wasSuppressed) {
      const priorSrc = String(existingOv.source_type || "").trim();
      const incSrcUpper = String(v || "").trim().toUpperCase();
      if (
        priorSrc &&
        (GEOMETRY_ONLY_FALLBACK_SOURCES.has(incSrcUpper) ||
          incSrcUpper.startsWith("FOOTPRINT ISSUE:"))
      ) {
        continue;
      }
    }
    if (k === "source_citation") {
      const cleanIncCit = stripEmbeddedFieldSurveyTag(stripEmbeddedGeometryTag(String(v || "")));
      const cleanPriorCit = stripEmbeddedFieldSurveyTag(
        stripEmbeddedGeometryTag(String(existingOv.source_citation || ""))
      );
      if (isGeomOnly && priorYr >= MIN_VALID_YEAR && !wasSuppressed && cleanPriorCit) {
        if (cleanIncCit && !cleanPriorCit.includes(cleanIncCit)) {
          merged.source_citation = `${cleanPriorCit} | Footprint note: ${cleanIncCit}`;
        } else {
          merged.source_citation = cleanPriorCit;
        }
        continue;
      }
      if (cleanIncCit !== "" || !("source_citation" in existingOv)) {
        merged.source_citation = cleanIncCit;
      }
      continue;
    }
    if (Array.isArray(v)) {
      if (v.length > 0 || !(k in existingOv)) merged[k] = v;
    } else if (typeof v === "number") {
      if (v > 0 || !(k in existingOv)) merged[k] = v;
    } else if (typeof v === "string") {
      if (v !== "" || !(k in existingOv)) merged[k] = v;
    } else if (v !== null && v !== undefined) {
      merged[k] = v;
    }
  }

  if (incomingOv.suppress_only) {
    const stashedGeom =
      existingOv.geometry || existingOv._pre_suppress_geometry || fallbackGeometry || null;
    delete merged._pre_suppress_geometry;
    return {
      ...merged,
      ...(stashedGeom ? { _pre_suppress_geometry: stashedGeom } : {}),
      ...(isSub ? { building_id: key, is_building_override: true } : {}),
      suppress_only: true,
      year_built: 0,
      decade: 0,
      geometry: null,
      keep_shard_footprints: false,
      good_brick_awards:
        incomingOv.good_brick_awards || existingOv.good_brick_awards || null,
      good_brick_summary:
        incomingOv.good_brick_summary || existingOv.good_brick_summary || "",
    };
  }

  merged.suppress_only = false;
  const isRevived = incomingYr >= MIN_VALID_YEAR;
  if (isRevived) {
    merged.year_built = incomingYr;
    merged.decade = computeNormalizedDecade(incomingYr);
  } else if (wasSuppressed) {
    merged.year_built = 0;
    merged.decade = 0;
  } else if (priorYr >= MIN_VALID_YEAR) {
    merged.year_built = priorYr;
    merged.decade = computeNormalizedDecade(priorYr);
  } else {
    merged.year_built = 0;
    merged.decade = 0;
  }

  const restoredGeom =
    incomingGeom ||
    existingOv.geometry ||
    existingOv._pre_suppress_geometry ||
    fallbackGeometry ||
    null;
  delete merged._pre_suppress_geometry;

  if (isSub) {
    merged.building_id = key;
    merged.is_building_override = true;
    if (restoredGeom) {
      merged.geometry = restoredGeom;
      merged.keep_shard_footprints = false;
      if (typeof incomingOv.replace_parcel_shards === "boolean") {
        merged.replace_parcel_shards = incomingOv.replace_parcel_shards;
      } else {
        merged.replace_parcel_shards = Boolean(existingOv.replace_parcel_shards);
      }
    } else {
      merged.geometry = null;
      merged.keep_shard_footprints = true;
      merged.replace_parcel_shards = false;
    }
  } else {
    merged.geometry = restoredGeom;
    if (typeof incomingOv.replace_parcel_shards === "boolean") {
      merged.replace_parcel_shards = incomingOv.replace_parcel_shards;
    }
    if (restoredGeom || ((isRevived || restoredGeom) && wasSuppressed && merged.keep_shard_footprints === false)) {
      delete merged.keep_shard_footprints;
    }
  }

  const mode = classifyOverrideFootprintMode(key, merged);
  if (mode === OVERRIDE_MODE_ADDITIVE_POLYGON) {
    merged.keep_shard_footprints = false;
    merged.replace_parcel_shards = false;
  } else if (mode === OVERRIDE_MODE_SHARD_PRIMARY && isSub) {
    merged.keep_shard_footprints = true;
    merged.replace_parcel_shards = false;
    merged.geometry = null;
  } else if (mode === OVERRIDE_MODE_SUPPRESS_VACANT) {
    merged.keep_shard_footprints = false;
    merged.geometry = null;
  }

  if (typeof merged.source_citation === "string" && merged.source_citation.includes(GEOMETRY_TAG_PREFIX)) {
    merged.source_citation = stripEmbeddedGeometryTag(merged.source_citation);
  }

  merged.good_brick_awards =
    incomingOv.good_brick_awards || existingOv.good_brick_awards || null;
  merged.good_brick_summary =
    incomingOv.good_brick_summary || existingOv.good_brick_summary || "";
  return merged;
}

/**
 * Merge curated override fields onto a feature's properties object if its `id` (`building_id`)
 * or `hcad_num` matches. Enforces OR-precedence so whole-parcel `suppress_only: true`
 * dominates any pre-existing `<hcad>#<slug>` sub-building record.
 */
export function applyOverrideToProperties(props, overridesMap) {
  if (!props || !overridesMap) return props;
  const bldId = String(props.building_id || "").trim();
  const featId = String(props.id || "").trim();
  const hcadNum =
    String(props.hcad_num || "").trim() ||
    cleanHcadPrefixFromKey(bldId) ||
    cleanHcadPrefixFromKey(featId);

  const parcelOv =
    hcadNum && overridesMap[hcadNum] && !overridesMap[hcadNum].is_building_override
      ? overridesMap[hcadNum]
      : null;

  let ov = null;
  if (parcelOv?.suppress_only) {
    ov = parcelOv;
  } else if (bldId && overridesMap[bldId]) {
    ov = overridesMap[bldId];
  } else if (featId && overridesMap[featId]) {
    ov = overridesMap[featId];
  } else if (parcelOv) {
    ov = parcelOv;
  }
  if (!ov) return props;

  const origYear = Number(ov.original_hcad_year) || Number(props.year_built) || 0;
  const isSuppressOnly = Boolean(ov.suppress_only);
  const verifiedYear = isSuppressOnly
    ? 0
    : Number(ov.year_built) || Number(props.year_built) || 0;
  const isGbPoint = Boolean(props.is_good_brick);
  const isTaxParcel =
    Boolean(props.is_tax_parcel) || /^p(?:cl|arcel)_/i.test(String(props.id || ""));
  const priorNonVacantUse =
    props._pre_vacant_use_category ||
    (props.use_category && props.use_category !== "Vacant / Exempt" ? props.use_category : "");

  const primaryAddr = (isGbPoint && props.address) || ov.address || props.address || "";
  const rawAltAddrs = [
    ...(Array.isArray(ov.alt_addresses) ? ov.alt_addresses : []),
    ...(Array.isArray(props.alt_addresses) ? props.alt_addresses : []),
    ov.address || "",
    props.address || "",
  ];
  const seenAddrLower = new Set(primaryAddr ? [String(primaryAddr).trim().toLowerCase()] : []);
  const altAddresses = [];
  for (const a of rawAltAddrs) {
    const cleanA = String(a || "").trim();
    if (!cleanA) continue;
    const lowA = cleanA.toLowerCase();
    if (!seenAddrLower.has(lowA)) {
      seenAddrLower.add(lowA);
      altAddresses.push(cleanA);
    }
  }

  const { _pre_vacant_use_category: _droppedStash, ...cleanBaseProps } = props;
  return {
    ...cleanBaseProps,
    id: isGbPoint || isTaxParcel ? props.id : ov.id || props.id || hcadNum,
    building_id: isTaxParcel ? "" : ov.building_id || ov.id || props.building_id || "",
    hcad_num: ov.hcad_num || hcadNum,
    year_built: verifiedYear,
    decade: isSuppressOnly ? 0 : computeNormalizedDecade(verifiedYear) || props.decade || 0,
    original_hcad_year: origYear,
    is_curated_override: Boolean(
      isSuppressOnly ||
        ov.source_type ||
        ov.source_citation ||
        ov.year_built ||
        props.is_curated_override
    ),
    is_building_override: Boolean(ov.is_building_override),
    replace_parcel_shards: Boolean(ov.replace_parcel_shards),
    keep_shard_footprints: isSuppressOnly ? false : Boolean(ov.keep_shard_footprints),
    suppress_only: isTaxParcel ? false : isSuppressOnly,
    ...(isTaxParcel
      ? {
          is_tax_parcel: true,
          is_demolished_vacant: isSuppressOnly,
          ...(isSuppressOnly && priorNonVacantUse
            ? { _pre_vacant_use_category: priorNonVacantUse }
            : {}),
        }
      : {}),
    suppress_shard_hcads: Array.isArray(ov.suppress_shard_hcads)
      ? ov.suppress_shard_hcads
      : Array.isArray(props.suppress_shard_hcads)
      ? props.suppress_shard_hcads
      : [],
    use_category:
      isTaxParcel && isSuppressOnly
        ? "Vacant / Exempt"
        : ov.use_category ||
          (isTaxParcel && props.is_demolished_vacant ? priorNonVacantUse : props.use_category) ||
          priorNonVacantUse ||
          "Residential",
    stories: Number(ov.stories) || Number(props.stories) || 1,
    height_m: Number(ov.height_m) || Number(props.height_m) || 4.5,
    address: primaryAddr,
    alt_addresses: altAddresses,
    historic_district: ov.historic_district || props.historic_district || "",
    neighborhood: ov.neighborhood || props.neighborhood || "",
    neighborhood_aliases:
      Array.isArray(ov.neighborhood_aliases) && ov.neighborhood_aliases.length
        ? ov.neighborhood_aliases
        : Array.isArray(props.neighborhood_aliases)
        ? props.neighborhood_aliases
        : [],
    super_neighborhood: ov.super_neighborhood || props.super_neighborhood || "",
    historic_ward: ov.historic_ward || props.historic_ward || "",
    subdivision: ov.subdivision || props.subdivision || "",
    contributing: ov.contributing || props.contributing || "",
    bld_style: ov.bld_style || props.bld_style || "",
    architect: ov.architect || props.architect || "",
    builder: ov.builder || props.builder || "",
    builder_source: ov.builder_source || props.builder_source || "",
    engineers: ov.engineers || props.engineers || "",
    original_permit: ov.original_permit || props.original_permit || "",
    original_cost: ov.original_cost || props.original_cost || "",
    yr_remodel: Number(ov.yr_remodel) || Number(props.yr_remodel) || 0,
    eff_year_built: Number(ov.eff_year_built) || Number(props.eff_year_built) || 0,
    was_relocated: Boolean(ov.was_relocated || props.was_relocated),
    relocation_note: ov.relocation_note || props.relocation_note || "",
    hist_contract: ov.hist_contract || props.hist_contract || null,
    landmark_name:
      (isGbPoint && props.landmark_name) || ov.landmark_name || props.landmark_name || "",
    building_name:
      ov.building_name || props.building_name || ov.landmark_name || props.landmark_name || "",
    alt_names: Array.isArray(ov.alt_names) && ov.alt_names.length
      ? ov.alt_names
      : Array.isArray(props.alt_names)
      ? props.alt_names
      : [],
    name_source:
      ov.name_source || props.name_source || "",
    landmark_type: ov.landmark_type || props.landmark_type || "",
    source_type: ov.source_type || props.source_type || "Preservation Houston Archival Record",
    source_citation: ov.source_citation || props.source_citation || "",
    source_url: ov.source_url || props.source_url || "",
    verified_by: ov.verified_by || props.verified_by || "Preservation Houston",
    override_updated_at: ov.updated_at || props.override_updated_at || "",
    good_brick_awards:
      (isGbPoint && props.good_brick_awards) ||
      ov.good_brick_awards ||
      props.good_brick_awards ||
      null,
    good_brick_summary:
      (isGbPoint && props.good_brick_summary) ||
      ov.good_brick_summary ||
      props.good_brick_summary ||
      "",
    landmark_report_url:
      ov.landmark_report_url ||
      props.landmark_report_url ||
      props.report_pdf_url ||
      "",
    landmark_code:
      ov.landmark_code ||
      props.landmark_code ||
      props.plm_num ||
      props.lm_num ||
      "",
    landmark_summary:
      ov.landmark_summary ||
      props.landmark_summary ||
      props.pdf_summary ||
      "",
    haif_threads:
      ov.haif_threads ||
      props.haif_threads ||
      null,
  };
}

/**
 * Normalize a property correction or Staff Admin override form payload so public suggestions
 * (`submitCorrectionSuggestion`) and 1-click Staff Admin approvals (`submitAdminApprovedOverride`)
 * share identical alias splitting, empty-vs-zero year handling, `source_type` composition,
 * and structured `source_citation` tags.
 */
export function normalizeCorrectionSubmission(payload = {}) {
  let validatedGeometry = null;
  let geometryGeojsonString = "";
  const rawGeomInput =
    payload.geometry !== undefined && payload.geometry !== null && payload.geometry !== ""
      ? payload.geometry
      : payload.geometry_geojson !== undefined &&
        payload.geometry_geojson !== null &&
        payload.geometry_geojson !== ""
      ? payload.geometry_geojson
      : null;

  if (rawGeomInput !== null) {
    let parsedGeom = rawGeomInput;
    if (typeof rawGeomInput === "string") {
      try {
        parsedGeom = JSON.parse(rawGeomInput);
      } catch (_err) {
        throw new Error("Invalid geometry_geojson payload: failed to parse JSON.");
      }
    }
    const vRes = validateOverridePolygonGeometry(parsedGeom);
    if (!vRes.ok) {
      throw new Error(`Invalid footprint polygon: ${vRes.errors.join("; ")}`);
    }
    validatedGeometry = vRes.geometry;
    geometryGeojsonString = JSON.stringify(validatedGeometry);
    if (geometryGeojsonString.length > MAX_GEOMETRY_JSON_CELL_CHARS) {
      throw new Error(
        `Footprint polygon exceeds Google Sheets ${MAX_GEOMETRY_JSON_CELL_CHARS}-character cell limit.`
      );
    }
  }

  const footprintIssue = String(payload.footprint_issue || "").trim();
  const footprintNotes = sanitizeAtlasTextJs(String(payload.footprint_notes || "").trim());
  const rawBldNameField = sanitizeAtlasTextJs(String(payload.building_name || "").trim());
  let primaryBuildingName = "";
  const splitAltNames = [];
  if (rawBldNameField) {
    const parts = rawBldNameField
      .split(/\s*[|;]\s*/)
      .map((s) => s.trim())
      .filter(Boolean);
    primaryBuildingName = parts[0] || "";
    for (const alt of parts.slice(1)) {
      if (!splitAltNames.includes(alt)) splitAltNames.push(alt);
    }
  }
  if (Array.isArray(payload.alt_names)) {
    for (const alt of payload.alt_names) {
      const cleanAlt = sanitizeAtlasTextJs(String(alt || "").trim());
      if (cleanAlt && cleanAlt !== primaryBuildingName && !splitAltNames.includes(cleanAlt)) {
        splitAltNames.push(cleanAlt);
      }
    }
  } else if (typeof payload.alt_names === "string" && payload.alt_names.trim()) {
    for (const alt of payload.alt_names.split(/\s*[|;]\s*/)) {
      const cleanAlt = sanitizeAtlasTextJs(alt.trim());
      if (cleanAlt && cleanAlt !== primaryBuildingName && !splitAltNames.includes(cleanAlt)) {
        splitAltNames.push(cleanAlt);
      }
    }
  }
  const combinedDisplayBldName = [primaryBuildingName, ...splitAltNames]
    .filter(Boolean)
    .join(" | ");

  const suggestedBuilder = sanitizeAtlasTextJs(String(payload.builder || "").trim());
  const suggestedPermitInfo = sanitizeAtlasTextJs(String(payload.permit_info || "").trim());
  const rawCitationInput = String(payload.source_citation || "").trim();
  if (!validatedGeometry && rawCitationInput.includes(GEOMETRY_TAG_PREFIX)) {
    const embeddedGeom = extractEmbeddedGeometryJson(rawCitationInput);
    if (embeddedGeom) {
      validatedGeometry = embeddedGeom;
      geometryGeojsonString = JSON.stringify(embeddedGeom);
    }
  }
  const rawCitation = stripEmbeddedFieldSurveyTag(
    stripEmbeddedGeometryTag(sanitizeAtlasTextJs(rawCitationInput))
  );
  const rawSourceType = sanitizeAtlasTextJs(
    String(
      payload.source_type ||
        (validatedGeometry ? "Satellite / Aerial Imagery" : "Houston City Directory")
    ).trim()
  );
  const satelliteUrl = String(payload.satellite_url || "").trim();

  const currentYr = Number(payload.current_year_built) || 0;
  const rawSuggStr = String(payload.suggested_year_built ?? "").trim();
  const explicitSuppress =
    payload.suppress_only === true ? true : payload.suppress_only === false ? false : null;
  const isDemolishedVacant = isDemolishedVacantOverrideInput({
    yearRaw: payload.suggested_year_built,
    footprintIssue,
    footprintNotes,
    sourceType: rawSourceType,
    sourceCitation: rawCitation,
    suppressOnly: explicitSuppress,
    hasGeometry: Boolean(validatedGeometry),
  });

  if (isDemolishedVacant) {
    validatedGeometry = null;
    geometryGeojsonString = "";
  }

  const effectiveFootprintIssue =
    isDemolishedVacant && !footprintIssue ? DEMOLISHED_VACANT_ISSUE : footprintIssue;

  const parsedSuggYr = rawSuggStr !== "" ? Number(rawSuggStr) : NaN;
  const hasPositiveSuggestedYr =
    Number.isFinite(parsedSuggYr) &&
    parsedSuggYr >= MIN_VALID_YEAR &&
    parsedSuggYr <= MAX_STANDING_OVERRIDE_YEAR;
  const suggestedYr = isDemolishedVacant
    ? 0
    : hasPositiveSuggestedYr
    ? Math.round(parsedSuggYr)
    : currentYr >= MIN_VALID_YEAR
    ? currentYr
    : "";

  const effectiveSourceType = effectiveFootprintIssue
    ? !isDemolishedVacant &&
      hasPositiveSuggestedYr &&
      (Math.round(parsedSuggYr) !== currentYr || Boolean(rawCitation))
      ? `${rawSourceType} + Footprint Issue (${effectiveFootprintIssue})`
      : `Footprint Issue: ${effectiveFootprintIssue}`
    : rawSourceType;

  const citationParts = [];
  if (isDemolishedVacant && !rawCitation.includes(DEMOLISHED_VACANT_CITATION_TAG)) {
    citationParts.push(DEMOLISHED_VACANT_CITATION_TAG);
  }
  if (combinedDisplayBldName) {
    citationParts.push(`[BUILDING NAME / HISTORICAL ALIAS]: ${combinedDisplayBldName}`);
  }
  if (suggestedBuilder) {
    citationParts.push(`[BUILDER / CONTRACTOR]: ${suggestedBuilder}`);
  }
  if (suggestedPermitInfo) {
    citationParts.push(`[PERMIT / CONTRACT RECORD]: ${suggestedPermitInfo}`);
  }
  if (effectiveFootprintIssue || footprintNotes) {
    const defaultNote = isDemolishedVacant
      ? "Building demolished; lot currently vacant."
      : "Flagged for building footprint review.";
    citationParts.push(
      `[FOOTPRINT / SHAPE ISSUE - ${effectiveFootprintIssue || "Geometry Error"}]: ${
        footprintNotes || defaultNote
      }`
    );
  }
  if (rawCitation) {
    citationParts.push(rawCitation);
  }

  const cleanSourceCitation = citationParts.join(" | ");
  const encodedSourceCitation = validatedGeometry
    ? encodeCitationWithGeometry(cleanSourceCitation, validatedGeometry)
    : cleanSourceCitation;

  return {
    primaryBuildingName,
    altNames: splitAltNames,
    combinedDisplayBldName,
    suggestedBuilder,
    suggestedPermitInfo,
    currentYr,
    suggestedYr,
    isDemolishedVacant: Boolean(isDemolishedVacant),
    validatedGeometry,
    geometryGeojsonString,
    effectiveFootprintIssue,
    effectiveFootprintNotes:
      footprintNotes ||
      (isDemolishedVacant ? "Building demolished; lot currently vacant." : ""),
    effectiveSourceType,
    effectiveSourceCitation: cleanSourceCitation,
    encodedSourceCitation,
    effectiveSourceUrl: String(payload.source_url || satelliteUrl || "").trim(),
  };
}

/**
 * Submit an end-user property data correction suggestion for moderator review.
 * Never mutates the live public dataset directly; posts with `status: "Pending"`
 * to the configured Google Sheet Apps Script webhook and stores a local copy.
 */
export async function submitCorrectionSuggestion(payload, webhookUrl = "") {
  const norm = normalizeCorrectionSubmission(payload);

  const record = {
    status: "Pending",
    submitted_at: new Date().toISOString().slice(0, 19).replace("T", " "),
    hcad_num: String(payload.hcad_num || "").trim(),
    address: String(payload.address || "").trim(),
    building_name: norm.primaryBuildingName,
    ...(norm.altNames.length ? { alt_names: norm.altNames.join("; ") } : {}),
    historic_district: String(payload.historic_district || "").trim(),
    contributing: String(payload.contributing || "").trim(),
    hcad_year_built: norm.currentYr,
    suggested_year_built: norm.suggestedYr,
    suppress_only: norm.isDemolishedVacant,
    bld_style: String(payload.bld_style || "").trim(),
    architect: String(payload.architect || "").trim(),
    builder: norm.suggestedBuilder,
    permit_info: norm.suggestedPermitInfo,
    source_type: norm.effectiveSourceType,
    source_citation: norm.encodedSourceCitation,
    source_url: norm.effectiveSourceUrl,
    footprint_issue: norm.effectiveFootprintIssue,
    footprint_notes: norm.effectiveFootprintNotes,
    ...(norm.geometryGeojsonString ? { geometry_geojson: norm.geometryGeojsonString } : {}),
    photo_url: String(payload.photo_url || "").trim(),
    photo_year: payload.photo_year ? Number(payload.photo_year) || String(payload.photo_year).trim() : "",
    photo_caption: String(payload.photo_caption || "").trim(),
    submitter_name: String(payload.submitter_name || "").trim(),
    submitter_email: String(payload.submitter_email || "").trim(),
  };

  // Save in local browser queue so the submitter or admin can view/export it
  const existing = getLocalPendingSuggestions();
  existing.unshift(record);
  if (typeof localStorage !== "undefined") {
    try {
      localStorage.setItem(LS_PENDING_SUGGESTIONS_KEY, JSON.stringify(existing.slice(0, 200)));
    } catch (_e) {
      // Ignore storage quota warnings
    }
  }

  let webhookDelivered = false;
  const targetUrl = String(webhookUrl || "").trim();
  if (targetUrl) {
    try {
      await fetch(targetUrl, {
        method: "POST",
        mode: "no-cors",
        headers: { "Content-Type": "text/plain;charset=utf-8" },
        body: JSON.stringify(record),
      });
      webhookDelivered = true;
    } catch (err) {
      console.warn("Webhook delivery warning (suggestion saved locally):", err);
    }
  }

  return {
    ok: true,
    webhookDelivered,
    record,
  };
}

/**
 * 1-Click Quick Flag reporter for:
 *   - `flagType: "photo"` (`PHOTO_MISATTRIBUTION_FLAG` - wrong building / misattributed Wikimedia or forum photo)
 *   - `flagType: "claim"` (`CLAIM_DISPUTE_FLAG` - disputed automated building name, architect, or rounded HCAD year)
 *   - `flagType: "narrative"` (`NARRATIVE_ERROR_FLAG` - historical narrative blurb error on a chapter, tour, or overlay)
 *
 * Immediately suppresses unverified claims locally in the user's browser and posts a structured
 * moderation row to the Preservation Houston Google Sheet (`Pending_Submissions`, or `Approved_Edits`
 * if an unlocked PH Admin explicitly passes `autoApproveIfAdmin: true`).
 */
export async function submitQuickFlagReport(payload = {}, webhookUrl = "") {
  const flagType = String(payload.flagType || "claim").trim().toLowerCase();
  const hcadNum = String(payload.hcad_num || payload.hcadNum || "").trim();
  const address = String(payload.address || "").trim();
  const district = String(payload.historic_district || payload.district || "").trim();
  const field = String(payload.field || "").trim().toLowerCase();
  const disputedValue = String(payload.disputed_value || payload.disputedValue || "").trim();
  const provenance = String(payload.provenance || "").trim();
  const photoUrl = String(payload.photo_url || payload.photoUrl || "").trim();
  const photoCaption = String(payload.photo_caption || payload.photoCaption || "").trim();
  const contextId = String(payload.context_id || payload.contextId || "").trim();
  const contextTitle = String(payload.context_title || payload.contextTitle || "").trim();
  const note = String(payload.note || "").trim();

  let sourceType = "CLAIM_DISPUTE_FLAG";
  const citationParts = [];

  if (flagType === "photo") {
    sourceType = "PHOTO_MISATTRIBUTION_FLAG";
    citationParts.push("[1-CLICK PHOTO FLAG: Wrong Building / Misattributed Photo]");
    if (provenance) citationParts.push(`[PHOTO PROVENANCE: ${provenance}]`);
    if (photoCaption) citationParts.push(`[CAPTION: ${photoCaption.slice(0, 140)}]`);
  } else if (flagType === "narrative") {
    sourceType = "NARRATIVE_ERROR_FLAG";
    citationParts.push(`[1-CLICK NARRATIVE FLAG: ${contextTitle || contextId || "Historical Blurb"}]`);
    if (disputedValue) citationParts.push(`[EXCERPT: ${disputedValue.slice(0, 160)}]`);
  } else {
    sourceType = "CLAIM_DISPUTE_FLAG";
    if (field) citationParts.push(`[DISPUTED FIELD: ${field}]`);
    if (disputedValue) citationParts.push(`[DISPUTED VALUE: ${disputedValue}]`);
    if (provenance) citationParts.push(`[PROVENANCE: ${provenance}]`);
    if (payload.suppressLocally !== false && field && disputedValue) {
      hideDisputedClaimLocally({ hcadNum, field, value: disputedValue });
    }
  }
  if (note) {
    citationParts.push(`[USER NOTE: ${note}]`);
  }

  const adminSession = getAdminSession();
  const isAdminDirect = Boolean(adminSession?.authorized && payload.autoApproveIfAdmin);

  const record = {
    ...(isAdminDirect
      ? {
          action: "admin_approve",
          status: "Approved",
          verified_by: adminSession.email || "Preservation Houston Admin Flag",
          admin_email: adminSession.email || "",
          google_id_token: adminSession.googleIdToken || "",
          admin_passkey: adminSession.adminPasskey || "",
        }
      : {
          status: "Pending",
        }),
    submitted_at: new Date().toISOString().slice(0, 19).replace("T", " "),
    hcad_num: hcadNum || (contextId ? `NARRATIVE_${contextId}` : "FLAG_REPORT"),
    address: address || contextTitle || "Narrative / Map Feature",
    building_name: flagType === "claim" && field === "building_name" ? disputedValue : contextTitle,
    historic_district: district,
    hcad_year_built: Number(payload.current_year_built) || 0,
    suggested_year_built: 0,
    bld_style: "",
    architect: flagType === "claim" && field === "architect" ? disputedValue : "",
    source_type: sourceType,
    source_citation: citationParts.join(" | "),
    source_url: String(payload.source_url || photoUrl || "").trim(),
    photo_url: photoUrl,
    photo_caption: photoCaption,
    submitter_name: String(payload.submitter_name || (adminSession?.email || "1-Click Map Flag")).trim(),
    submitter_email: String(payload.submitter_email || "").trim(),
  };

  const existing = getLocalPendingSuggestions();
  existing.unshift(record);
  if (typeof localStorage !== "undefined") {
    try {
      localStorage.setItem(LS_PENDING_SUGGESTIONS_KEY, JSON.stringify(existing.slice(0, 200)));
    } catch (_e) {
      // Ignore storage quota warnings
    }
  }

  let webhookDelivered = false;
  const targetUrl = String(webhookUrl || "").trim();
  if (targetUrl) {
    try {
      await fetch(targetUrl, {
        method: "POST",
        mode: isAdminDirect ? "cors" : "no-cors",
        headers: { "Content-Type": "text/plain;charset=utf-8" },
        body: JSON.stringify(record),
      });
      webhookDelivered = true;
    } catch (err) {
      console.warn("Quick-flag webhook delivery warning (saved locally):", err);
    }
  }

  return {
    ok: true,
    webhookDelivered,
    approvedGlobally: isAdminDirect && webhookDelivered,
    record,
  };
}

/**
 * Retrieve pending correction suggestions saved in this browser session.
 */
export function getLocalPendingSuggestions() {
  if (typeof localStorage === "undefined") return [];
  try {
    const raw = localStorage.getItem(LS_PENDING_SUGGESTIONS_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch (_e) {
    return [];
  }
}

/**
 * Format an array of suggestion/override records as an RFC 4180 CSV string
 * matching the Preservation Houston Google Sheet column layout.
 */
export function formatSuggestionsAsCsv(records) {
  const cols = [
    "status",
    "hcad_num",
    "address",
    "historic_district",
    "hcad_year_built",
    "suggested_year_built",
    "bld_style",
    "architect",
    "source_type",
    "source_citation",
    "source_url",
    "footprint_issue",
    "footprint_notes",
    "photo_url",
    "photo_year",
    "photo_caption",
    "submitter_name",
    "submitter_email",
    "submitted_at",
  ];
  const escapeCell = (val) => {
    const s = String(val ?? "");
    if (s.includes(",") || s.includes('"') || s.includes("\n")) {
      return `"${s.replace(/"/g, '""')}"`;
    }
    return s;
  };
  const lines = [cols.join(",")];
  for (const r of records) {
    lines.push(cols.map((c) => escapeCell(r[c])).join(","));
  }
  return lines.join("\n");
}

const LS_ADMIN_SESSION_KEY = "ph_atlas_admin_session";

/**
 * Return the active Preservation Houston Admin session if unlocked in this browser.
 */
export function getAdminSession() {
  try {
    const raw =
      (typeof sessionStorage !== "undefined" && sessionStorage.getItem(LS_ADMIN_SESSION_KEY)) ||
      (typeof localStorage !== "undefined" && localStorage.getItem(LS_ADMIN_SESSION_KEY));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (parsed && parsed.authorized) return parsed;
    return null;
  } catch (_e) {
    return null;
  }
}

/**
 * Clear the active Admin session (lock Admin Mode).
 */
export function clearAdminSession() {
  if (typeof sessionStorage !== "undefined") {
    sessionStorage.removeItem(LS_ADMIN_SESSION_KEY);
  }
  if (typeof localStorage !== "undefined") {
    localStorage.removeItem(LS_ADMIN_SESSION_KEY);
  }
}

/**
 * Authenticate a Preservation Houston Administrator against the Google Sheet's
 * bound Apps Script (`action: "verify_admin"`), which checks either:
 *   1. A Google-signed OAuth/ID token whose email is in `Spreadsheet.getEditors()` / `getOwner()`, or
 *   2. The private Sheet Editor Passkey stored inside cell B6 of `Instructions_&_Apps_Script`
 *      (accessible only to Google accounts with Editor access on the Restricted Sheet).
 */
export async function authenticateAdminSession({
  googleIdToken = "",
  adminPasskey = "",
  adminEmail = "",
  webhookUrl = "",
}) {
  const token = String(googleIdToken || "").trim();
  const passkey = String(adminPasskey || "").trim();
  const email = String(adminEmail || "").trim();
  if (!token && (!email || !passkey)) {
    return {
      ok: false,
      error: "Please enter both your Sheet Editor Google Account Email and the Sheet Editor Passkey.",
    };
  }

  const targetUrl = String(webhookUrl || "").trim();
  let verifiedByWebhook = false;
  let resolvedEmail = email || "Preservation Houston Sheet Editor";

  if (targetUrl) {
    try {
      const res = await fetch(targetUrl, {
        method: "POST",
        headers: { "Content-Type": "text/plain;charset=utf-8" },
        body: JSON.stringify({
          action: "verify_admin",
          google_id_token: token,
          admin_passkey: passkey,
          admin_email: email,
        }),
      });
      if (!res.ok) {
        return { ok: false, error: `Verification webhook HTTP ${res.status}` };
      }
      const data = await res.json();
      if (!data.authorized) {
        return {
          ok: false,
          error:
            data.error ||
            "Access denied: Google account is not listed as an Editor on the Curated Overrides Google Sheet.",
        };
      }
      verifiedByWebhook = true;
      resolvedEmail = data.email || resolvedEmail;
    } catch (err) {
      return {
        ok: false,
        error: `Could not reach Google Sheet verification service: ${err.message || err}`,
      };
    }
  }

  const session = {
    authorized: true,
    verifiedByWebhook,
    email: resolvedEmail,
    googleIdToken: token,
    adminPasskey: passkey,
    unlockedAt: new Date().toISOString(),
  };

  if (typeof sessionStorage !== "undefined") {
    sessionStorage.setItem(LS_ADMIN_SESSION_KEY, JSON.stringify(session));
  }
  if (typeof localStorage !== "undefined") {
    localStorage.setItem(LS_ADMIN_SESSION_KEY, JSON.stringify(session));
  }
  return { ok: true, session };
}

/**
 * Submit an Administrator-Approved override directly (`action: "admin_approve"`).
 * The Google Apps Script verifies `google_id_token` (against `Spreadsheet.getEditors()`)
 * or `admin_email` + `admin_passkey` server-side before writing `status = "Approved"`.
 */
export async function submitAdminApprovedOverride(payload, webhookUrl = "") {
  const session = getAdminSession();
  if (!session || !session.authorized) {
    throw new Error("Admin session required to publish approved overrides.");
  }

  const norm = normalizeCorrectionSubmission(payload);
  const resolvedYearBuilt = norm.isDemolishedVacant ? 0 : Number(norm.suggestedYr) || 0;
  const explicitReplace =
    payload.replace_parcel_shards === true
      ? true
      : payload.replace_parcel_shards === false
      ? false
      : null;

  const record = {
    action: "admin_approve",
    status: "Approved",
    submitted_at: new Date().toISOString().slice(0, 10),
    hcad_num: String(payload.hcad_num || "").trim(),
    address: String(payload.address || "").trim().toUpperCase(),
    building_name: norm.primaryBuildingName,
    ...(norm.altNames.length ? { alt_names: norm.altNames.join("; ") } : {}),
    historic_district: String(payload.historic_district || "").trim(),
    contributing: String(payload.contributing || "").trim(),
    hcad_year_built: norm.currentYr,
    suggested_year_built: norm.suggestedYr,
    suppress_only: norm.isDemolishedVacant,
    bld_style: String(payload.bld_style || "").trim(),
    architect: String(payload.architect || "").trim(),
    builder: norm.suggestedBuilder,
    permit_info: norm.suggestedPermitInfo,
    footprint_issue: norm.effectiveFootprintIssue,
    footprint_notes: norm.effectiveFootprintNotes,
    ...(norm.geometryGeojsonString ? { geometry_geojson: norm.geometryGeojsonString } : {}),
    ...(explicitReplace !== null ? { replace_parcel_shards: explicitReplace } : {}),
    source_type: norm.effectiveSourceType,
    source_citation: norm.encodedSourceCitation,
    source_url: norm.effectiveSourceUrl,
    verified_by: session.email || "Preservation Houston Archival Review",
    admin_email: session.email || "",
    google_id_token: session.googleIdToken || "",
    admin_passkey: session.adminPasskey || "",
  };

  let webhookDelivered = false;
  const targetUrl = String(webhookUrl || "").trim();
  if (targetUrl) {
    const res = await fetch(targetUrl, {
      method: "POST",
      headers: { "Content-Type": "text/plain;charset=utf-8" },
      body: JSON.stringify(record),
    });
    if (!res.ok) {
      throw new Error(`Admin approval webhook HTTP ${res.status}`);
    }
    const data = await res.json();
    if (!data || !data.authorized) {
      throw new Error((data && data.error) || "Server rejected admin approval credentials.");
    }
    if (data.ok === false) {
      throw new Error(data.error || "Server rejected admin override payload.");
    }
    webhookDelivered = true;
  }

  const rawKey = record.hcad_num;
  const isBuildingOverride = rawKey.includes("#");
  const cleanHcadNum = isBuildingOverride ? rawKey.split("#")[0].trim() : rawKey;
  const hasCustomGeom = Boolean(norm.validatedGeometry && !norm.isDemolishedVacant);

  const override = {
    id: rawKey,
    building_id: isBuildingOverride ? rawKey : "",
    hcad_num: cleanHcadNum,
    is_building_override: isBuildingOverride,
    suppress_only: norm.isDemolishedVacant,
    ...(norm.isDemolishedVacant
      ? { geometry: null, keep_shard_footprints: false }
      : hasCustomGeom
      ? {
          geometry: norm.validatedGeometry,
          ...(isBuildingOverride
            ? {
                keep_shard_footprints: false,
                replace_parcel_shards: explicitReplace === true,
              }
            : explicitReplace !== null
            ? { replace_parcel_shards: explicitReplace }
            : {}),
        }
      : isBuildingOverride
      ? { keep_shard_footprints: true, replace_parcel_shards: false }
      : {}),
    address: record.address,
    ...(norm.primaryBuildingName ? { building_name: norm.primaryBuildingName } : {}),
    ...(norm.altNames.length ? { alt_names: norm.altNames } : {}),
    ...(norm.suggestedBuilder ? { builder: norm.suggestedBuilder } : {}),
    ...(norm.suggestedPermitInfo ? { original_permit: norm.suggestedPermitInfo } : {}),
    historic_district: record.historic_district,
    contributing: record.contributing,
    year_built: resolvedYearBuilt,
    decade: norm.isDemolishedVacant ? 0 : computeNormalizedDecade(resolvedYearBuilt),
    original_hcad_year: record.hcad_year_built,
    bld_style: record.bld_style,
    architect: record.architect,
    source_type: record.source_type,
    source_citation: norm.effectiveSourceCitation,
    source_url: record.source_url,
    verified_by: record.verified_by,
    updated_at: record.submitted_at,
  };

  return {
    ok: true,
    webhookDelivered,
    overrideKey: rawKey,
    override,
  };
}

// ============================================================================
// Walk-the-Block Field Survey Persistence, Formula-Safe CSV & IndexedDB Photos
// ============================================================================

let _inMemoryFieldSurveyFallback = [];
let _fieldSurveyStorageOverride = null;
const _inMemoryPhotoFallback = new Map();

export function _setFieldSurveyStorageOverrideForTests(storageOrNull = null) {
  _fieldSurveyStorageOverride = storageOrNull;
  _inMemoryFieldSurveyFallback = [];
  _inMemoryPhotoFallback.clear();
}

function getActiveFieldSurveyStorage() {
  if (_fieldSurveyStorageOverride) return _fieldSurveyStorageOverride;
  try {
    if (typeof localStorage !== "undefined" && localStorage) return localStorage;
  } catch (_e) {
    // Private browsing or sandboxed iframe blocking localStorage getter
  }
  return null;
}

/**
 * Neutralize spreadsheet formula triggers (`=`, `+`, `-`, `@`, `\t`, `\r`) and
 * typographic dashes so exported CSVs open safely in Excel and Google Sheets (DF-11).
 */
export function sanitizeSpreadsheetCell(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "number") {
    return Number.isFinite(value) ? String(value) : "";
  }
  let s = sanitizeAtlasTextJs(String(value)).replace(/\r\n/g, " ").replace(/\r/g, " ");
  if (!s) return "";
  const firstRaw = s[0];
  const trimmedLeft = s.trimStart();
  const firstTrimmed = trimmedLeft ? trimmedLeft[0] : "";
  if (
    firstRaw === "=" ||
    firstRaw === "+" ||
    firstRaw === "-" ||
    firstRaw === "@" ||
    firstRaw === "\t" ||
    firstTrimmed === "=" ||
    firstTrimmed === "+" ||
    firstTrimmed === "-" ||
    firstTrimmed === "@"
  ) {
    s = `'${s}`;
  }
  return s;
}

/**
 * Export Walk-the-Block survey records as formula-neutralized RFC 4180 CSV matching
 * the exact 18 data columns (`A:R`) of the `Field_Surveys` Google Sheet tab.
 */
export function exportFieldSurveysToCsv(records = []) {
  const cols = FIELD_SURVEY_CSV_COLUMNS;
  const escapeRfc4180 = (rawVal) => {
    const safe = sanitizeSpreadsheetCell(rawVal);
    if (safe.includes(",") || safe.includes('"') || safe.includes("\n") || safe.includes("\r")) {
      return `"${safe.replace(/"/g, '""')}"`;
    }
    return safe;
  };
  const lines = [cols.join(",")];
  for (const rec of Array.isArray(records) ? records : []) {
    if (!rec || typeof rec !== "object") continue;
    lines.push(cols.map((col) => escapeRfc4180(rec[col] ?? "")).join(","));
  }
  return lines.join("\n");
}

function deduplicateSurveyRecordsById(list) {
  const byId = new Map();
  const order = [];
  for (const item of Array.isArray(list) ? list : []) {
    if (!item || typeof item !== "object") continue;
    const sid = String(item.survey_id || "").trim();
    if (!sid) continue;
    if (!byId.has(sid)) {
      byId.set(sid, { ...item });
      order.push(sid);
    } else {
      byId.set(sid, { ...byId.get(sid), ...item });
    }
  }
  return order.map((sid) => byId.get(sid));
}

export function getLocalFieldSurveys() {
  const storage = getActiveFieldSurveyStorage();
  if (storage) {
    try {
      const raw = storage.getItem(LS_FIELD_SURVEYS_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed)) {
          const deduped = deduplicateSurveyRecordsById(parsed);
          if (_inMemoryFieldSurveyFallback.length === 0) {
            return deduped;
          }
          return deduplicateSurveyRecordsById([...deduped, ..._inMemoryFieldSurveyFallback]);
        }
      }
    } catch (_e) {
      // Fall back cleanly to session memory below
    }
  }
  return deduplicateSurveyRecordsById(_inMemoryFieldSurveyFallback);
}

function writeFieldSurveyList(records) {
  const deduped = deduplicateSurveyRecordsById(records).slice(-1000);
  const storage = getActiveFieldSurveyStorage();
  if (storage) {
    try {
      storage.setItem(LS_FIELD_SURVEYS_KEY, JSON.stringify(deduped));
      _inMemoryFieldSurveyFallback = deduped;
      return { persistedToStorage: true, usedFallbackMemory: false, records: deduped };
    } catch (_e) {
      _inMemoryFieldSurveyFallback = deduped;
      return { persistedToStorage: false, usedFallbackMemory: true, records: deduped };
    }
  }
  _inMemoryFieldSurveyFallback = deduped;
  return { persistedToStorage: false, usedFallbackMemory: true, records: deduped };
}

export function saveLocalFieldSurvey(record = {}) {
  const sid = String(record.survey_id || "").trim();
  if (!sid) {
    throw new Error("saveLocalFieldSurvey requires a non-empty survey_id.");
  }
  const cleanRec = sanitizePayloadStringsJs({
    review_status: "Unreviewed",
    sync_status: "queued",
    ...record,
    survey_id: sid,
  });
  const current = getLocalFieldSurveys();
  const idx = current.findIndex((r) => r.survey_id === sid);
  if (idx >= 0) {
    current[idx] = { ...current[idx], ...cleanRec };
  } else {
    current.push(cleanRec);
  }
  const writeRes = writeFieldSurveyList(current);
  return {
    ok: true,
    record: writeRes.records.find((r) => r.survey_id === sid) || cleanRec,
    persistedToStorage: writeRes.persistedToStorage,
    usedFallbackMemory: writeRes.usedFallbackMemory,
  };
}

export function removeLocalFieldSurvey(surveyId) {
  const sid = String(surveyId || "").trim();
  if (!sid) return { ok: false, removed: null };
  const current = getLocalFieldSurveys();
  const removed = current.find((r) => r.survey_id === sid) || null;
  const remaining = current.filter((r) => r.survey_id !== sid);
  writeFieldSurveyList(remaining);
  return { ok: Boolean(removed), removed };
}

export function clearSyncedFieldSurveys() {
  const remaining = getLocalFieldSurveys().filter((r) => r.sync_status !== "synced");
  const writeRes = writeFieldSurveyList(remaining);
  return { ok: true, remainingCount: writeRes.records.length };
}

export function buildFieldSurveyWebhookRecord(draft = {}) {
  const sid =
    String(draft.survey_id || "").trim() ||
    `SURV_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`;
  const surveyedAt =
    String(draft.surveyed_at || "").trim() ||
    new Date().toISOString().slice(0, 19).replace("T", " ");
  const atlasYrNum = Number(draft.atlas_year_built);
  const atlasYr =
    Number.isFinite(atlasYrNum) && atlasYrNum > 0 ? Math.round(atlasYrNum) : "";

  const envelope = {};
  for (const col of FIELD_SURVEY_CSV_COLUMNS) {
    if (col === "survey_id") {
      envelope[col] = sid;
    } else if (col === "surveyed_at") {
      envelope[col] = surveyedAt;
    } else if (col === "review_status") {
      envelope[col] = sanitizeAtlasTextJs(String(draft.review_status || "Unreviewed").trim());
    } else if (col === "atlas_year_built") {
      envelope[col] = atlasYr;
    } else {
      envelope[col] = sanitizeAtlasTextJs(String(draft[col] ?? "").trim());
    }
  }

  const summaryBits = [
    `[WALK-THE-BLOCK SURVEY ${sid}]`,
    envelope.integrity_grade ? `Integrity: ${envelope.integrity_grade}` : "",
    envelope.condition_rating ? `Condition: ${envelope.condition_rating}` : "",
    envelope.suggested_contributing ? `Status: ${envelope.suggested_contributing}` : "",
    envelope.observed_year_or_demo_note ? `Observed: ${envelope.observed_year_or_demo_note}` : "",
    envelope.field_notes ? `Notes: ${envelope.field_notes}` : "",
  ].filter(Boolean);
  const sourceCitation = encodeFieldSurveyEnvelope(summaryBits.join(" | "), envelope);

  return {
    envelope,
    webhookPayload: {
      action: "field_survey",
      status: "Pending",
      ...envelope,
      submitted_at: envelope.surveyed_at,
      historic_district: envelope.district_or_corridor,
      contributing: envelope.suggested_contributing,
      hcad_year_built: envelope.atlas_year_built,
      suggested_year_built: "",
      suppress_only: false,
      source_type: FIELD_SURVEY_SOURCE_TYPE,
      source_citation: sourceCitation,
      source_url: envelope.photo_url,
      submitter_name: envelope.surveyor_name,
    },
  };
}

/**
 * Persist a Walk-the-Block field survey locally immediately and attempt a CORS-readable
 * `text/plain` POST to the Google Sheet Apps Script webhook, marking `sync_status = "synced"`
 * STRICTLY when the server acknowledges `{ ok: true }` (DF-4, DF-11).
 */
export async function submitFieldSurveyObservation(
  draft = {},
  webhookUrl = "",
  { fetchImpl = null, skipNetwork = false, requireExistingLocal = false } = {}
) {
  const { envelope, webhookPayload } = buildFieldSurveyWebhookRecord(draft);
  const stillExistsLocally = () =>
    getLocalFieldSurveys().some((r) => r.survey_id === envelope.survey_id);

  if (requireExistingLocal && !stillExistsLocally()) {
    return {
      ok: false,
      abortedByUndo: true,
      webhookDelivered: false,
      queuedOffline: false,
      usedFallbackMemory: false,
      record: null,
      webhookPayload,
    };
  }

  const localSave = saveLocalFieldSurvey({
    ...envelope,
    ...(draft.local_photo_id ? { local_photo_id: String(draft.local_photo_id) } : {}),
    ...(draft.local_photo_filename ? { local_photo_filename: String(draft.local_photo_filename) } : {}),
    sync_status: "queued",
    last_sync_error: "",
  });

  const maybeUpdateQueuedError = (errMsg) => {
    if (requireExistingLocal && !stillExistsLocally()) {
      return { record: localSave.record, usedFallbackMemory: localSave.usedFallbackMemory };
    }
    return saveLocalFieldSurvey({
      ...localSave.record,
      sync_status: "queued",
      last_sync_error: errMsg,
    });
  };

  const targetUrl = String(webhookUrl || "").trim();
  const isOffline =
    skipNetwork ||
    (typeof navigator !== "undefined" && navigator && navigator.onLine === false);
  const doFetch = fetchImpl || (typeof fetch === "function" ? fetch : null);

  if (!targetUrl || isOffline || !doFetch) {
    return {
      ok: true,
      webhookDelivered: false,
      queuedOffline: true,
      usedFallbackMemory: localSave.usedFallbackMemory,
      record: localSave.record,
      webhookPayload,
    };
  }

  try {
    const res = await doFetch(targetUrl, {
      method: "POST",
      headers: { "Content-Type": "text/plain;charset=utf-8" },
      body: JSON.stringify(webhookPayload),
    });
    if (!res || !res.ok) {
      const errMsg = `Field survey webhook HTTP ${res ? res.status : "network_error"}`;
      const updated = maybeUpdateQueuedError(errMsg);
      return {
        ok: true,
        webhookDelivered: false,
        queuedOffline: true,
        error: errMsg,
        usedFallbackMemory: updated.usedFallbackMemory,
        record: updated.record,
        webhookPayload,
      };
    }

    let bodyJson = null;
    try {
      bodyJson = typeof res.json === "function" ? await res.json() : JSON.parse(await res.text());
    } catch (_parseErr) {
      bodyJson = null;
    }

    if (!bodyJson || bodyJson.ok !== true) {
      const errMsg =
        (bodyJson && bodyJson.error) ||
        "Webhook response did not return verified { ok: true } JSON acknowledgment.";
      const updated = maybeUpdateQueuedError(errMsg);
      return {
        ok: true,
        webhookDelivered: false,
        queuedOffline: true,
        error: errMsg,
        usedFallbackMemory: updated.usedFallbackMemory,
        record: updated.record,
        webhookPayload,
      };
    }

    if (requireExistingLocal && !stillExistsLocally()) {
      return {
        ok: true,
        abortedByUndo: true,
        webhookDelivered: true,
        queuedOffline: false,
        usedFallbackMemory: false,
        record: localSave.record,
        webhookPayload,
        serverResponse: bodyJson,
      };
    }

    const syncedAt = new Date().toISOString();
    const syncedSave = saveLocalFieldSurvey({
      ...localSave.record,
      sync_status: "synced",
      synced_at: syncedAt,
      target_tab: bodyJson.target_tab || "Field_Surveys",
      last_sync_error: "",
    });
    return {
      ok: true,
      webhookDelivered: true,
      queuedOffline: false,
      usedFallbackMemory: syncedSave.usedFallbackMemory,
      record: syncedSave.record,
      webhookPayload,
      serverResponse: bodyJson,
    };
  } catch (err) {
    const errMsg = sanitizeAtlasTextJs(err?.message || String(err));
    const updated = maybeUpdateQueuedError(errMsg);
    return {
      ok: true,
      webhookDelivered: false,
      queuedOffline: true,
      error: errMsg,
      usedFallbackMemory: updated.usedFallbackMemory,
      record: updated.record,
      webhookPayload,
    };
  }
}

/**
 * Flush all unsynced Walk-the-Block field surveys sequentially to the Google Sheet webhook,
 * preserving deterministic `survey_id` idempotency across network retries.
 */
export async function flushPendingFieldSurveys(webhookUrl = "", options = {}) {
  const all = getLocalFieldSurveys();
  const pending = all.filter((r) => r.sync_status !== "synced");
  let syncedCount = 0;
  const errors = [];
  for (const item of pending) {
    const res = await submitFieldSurveyObservation(item, webhookUrl, options);
    if (res.webhookDelivered) {
      syncedCount += 1;
    } else if (res.error) {
      errors.push({ survey_id: item.survey_id, error: res.error });
    }
  }
  const after = getLocalFieldSurveys();
  const remainingCount = after.filter((r) => r.sync_status !== "synced").length;
  return {
    totalPendingBefore: pending.length,
    syncedCount,
    remainingCount,
    errors,
  };
}

function openFieldSurveyPhotosIdb() {
  if (typeof indexedDB === "undefined" || !indexedDB) {
    return Promise.resolve(null);
  }
  return new Promise((resolve) => {
    try {
      const req = indexedDB.open(IDB_FIELD_SURVEY_PHOTOS_DB, 1);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(IDB_FIELD_SURVEY_PHOTOS_STORE)) {
          db.createObjectStore(IDB_FIELD_SURVEY_PHOTOS_STORE, { keyPath: "photoId" });
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => resolve(null);
    } catch (_e) {
      resolve(null);
    }
  });
}

export async function saveSurveyPhotoBlob(entry = {}) {
  const photoId =
    String(entry.photoId || "").trim() ||
    `PHOTO_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`;
  const record = {
    photoId,
    surveyId: String(entry.surveyId || "").trim(),
    filename: String(entry.filename || `${photoId}.jpg`).trim(),
    hcadNum: String(entry.hcadNum || "").trim(),
    address: String(entry.address || "").trim(),
    capturedAt: String(entry.capturedAt || new Date().toISOString()),
    mimeType: String(entry.mimeType || "image/jpeg"),
    blob: entry.blob || null,
    dataUrl: String(entry.dataUrl || ""),
    width: Number(entry.width) || 0,
    height: Number(entry.height) || 0,
    byteLength: Number(entry.byteLength || entry.blob?.size || 0),
  };

  const db = await openFieldSurveyPhotosIdb();
  if (db) {
    const wroteIdb = await new Promise((resolve) => {
      try {
        const tx = db.transaction(IDB_FIELD_SURVEY_PHOTOS_STORE, "readwrite");
        tx.objectStore(IDB_FIELD_SURVEY_PHOTOS_STORE).put(record);
        tx.oncomplete = () => resolve(true);
        tx.onerror = () => resolve(false);
        tx.onabort = () => resolve(false);
      } catch (_e) {
        resolve(false);
      }
    });
    try {
      db.close();
    } catch (_e) {
      // Ignore close errors
    }
    if (wroteIdb) {
      return { ok: true, photoId, persistedToIndexedDb: true, record };
    }
  }
  _inMemoryPhotoFallback.set(photoId, record);
  return { ok: true, photoId, persistedToIndexedDb: false, record };
}

export async function getSurveyPhotoBlob(photoId) {
  const key = String(photoId || "").trim();
  if (!key) return null;
  const db = await openFieldSurveyPhotosIdb();
  if (db) {
    const found = await new Promise((resolve) => {
      try {
        const tx = db.transaction(IDB_FIELD_SURVEY_PHOTOS_STORE, "readonly");
        const req = tx.objectStore(IDB_FIELD_SURVEY_PHOTOS_STORE).get(key);
        req.onsuccess = () => resolve(req.result || null);
        req.onerror = () => resolve(null);
      } catch (_e) {
        resolve(null);
      }
    });
    try {
      db.close();
    } catch (_e) {
      // Ignore close errors
    }
    if (found) return found;
  }
  return _inMemoryPhotoFallback.get(key) || null;
}

export async function listSurveyPhotoSummaries() {
  const out = [];
  const seen = new Set();
  const db = await openFieldSurveyPhotosIdb();
  if (db) {
    const rows = await new Promise((resolve) => {
      try {
        const tx = db.transaction(IDB_FIELD_SURVEY_PHOTOS_STORE, "readonly");
        const req = tx.objectStore(IDB_FIELD_SURVEY_PHOTOS_STORE).getAll();
        req.onsuccess = () => resolve(Array.isArray(req.result) ? req.result : []);
        req.onerror = () => resolve([]);
      } catch (_e) {
        resolve([]);
      }
    });
    try {
      db.close();
    } catch (_e) {
      // Ignore close errors
    }
    for (const r of rows) {
      if (r && r.photoId && !seen.has(r.photoId)) {
        seen.add(r.photoId);
        const { blob: _b, dataUrl: _d, ...summary } = r;
        out.push(summary);
      }
    }
  }
  for (const [pid, r] of _inMemoryPhotoFallback.entries()) {
    if (!seen.has(pid)) {
      seen.add(pid);
      const { blob: _b, dataUrl: _d, ...summary } = r;
      out.push(summary);
    }
  }
  out.sort((a, b) => String(a.capturedAt || "").localeCompare(String(b.capturedAt || "")));
  return out;
}

export async function deleteSurveyPhotoBlob(photoId) {
  const key = String(photoId || "").trim();
  if (!key) return false;
  _inMemoryPhotoFallback.delete(key);
  const db = await openFieldSurveyPhotosIdb();
  if (db) {
    await new Promise((resolve) => {
      try {
        const tx = db.transaction(IDB_FIELD_SURVEY_PHOTOS_STORE, "readwrite");
        tx.objectStore(IDB_FIELD_SURVEY_PHOTOS_STORE).delete(key);
        tx.oncomplete = () => resolve(true);
        tx.onerror = () => resolve(false);
        tx.onabort = () => resolve(false);
      } catch (_e) {
        resolve(false);
      }
    });
    try {
      db.close();
    } catch (_e) {
      // Ignore close errors
    }
  }
  return true;
}

