/**
 * Domain geometry, City of Houston Chapter 42 statutory math, live HCAD blockface fetchers,
 * scorecard wire decoder, and organizer HTML template builders for:
 *   1. Shareable Neighborhood & Council District Preservation Scorecards (#scorecard=<layer>:<id>)
 *   2. City of Houston Chapter 42 SMLS / SMBL Block-Face Eligibility Calculator & Civic Club Kit (#ch42=<hcad13>)
 *
 * Statutory basis verified against City of Houston Code of Ordinances Chapter 42, Article III:
 *   - Sec. 42-197..208: Special Minimum Lot Size (SMLSB Block / SMLSA Area)
 *   - Sec. 42-170..178: Special Minimum Building Line (SMBLB Block / SMBLA Area)
 *   - Ord. No. 2015-639 (effective July 24, 2015): extended newly enacted terms from 20 to 40 years
 *     while leaving pre-July 24, 2015 ordinances on their 20-year statutory sunset clock.
 */

import { isEntitySuppressedByOverrides } from "./curatedEdits.js?v=20261030a";
import { HCAD_PUBLIC_QUERY_URL, normalizeHcad13 } from "./hcadViewportParcels.js?v=20261030a";

export const ORGANIZER_CACHE_BUST = "20261030a";
export const NEIGHBORHOOD_SCORECARDS_URL = `public/data/neighborhood_scorecards.json?v=${ORGANIZER_CACHE_BUST}`;

export const ORD_2015_639_CUTOFF_ISO = "2015-07-24";
export const ORD_2015_639_CUTOFF_NUM = 639;
export const CH42_CROSS_STREET_GAP_METERS = 16.0;
export const CH42_PRIMARY_FOOTPRINT_MIN_SQFT = 400.0;
export const CH42_RIBBON_RATIO_THRESHOLD_FT = 20.0;
export const CH42_RIBBON_BUFFER_FT = 26.0;
export const CH42_BLOCKFACE_ROLE_PROP = "ch42_role";

export const COH_COUNCIL_ROSTER_2024_2028 = Object.freeze({
  A: {
    member: "Amy Peck",
    phone: "832-393-3010",
    email: "districta@houstontx.gov",
    url: "https://www.houstontx.gov/council/a/index.html",
  },
  B: {
    member: "Tarsha Jackson",
    phone: "832-393-3009",
    email: "districtb@houstontx.gov",
    url: "https://www.houstontx.gov/council/b/index.html",
  },
  C: {
    member: "Joe Panzarella",
    phone: "832-393-3004",
    email: "districtc@houstontx.gov",
    url: "https://www.houstontx.gov/council/c/index.html",
  },
  D: {
    member: "Carolyn Evans-Shabazz",
    phone: "832-393-3001",
    email: "districtd@houstontx.gov",
    url: "https://www.houstontx.gov/council/d/index.html",
  },
  E: {
    member: "Fred Flickinger",
    phone: "832-393-3008",
    email: "districte@houstontx.gov",
    url: "https://www.houstontx.gov/council/e/index.html",
  },
  F: {
    member: "Tiffany D. Thomas",
    phone: "832-393-3002",
    email: "districtf@houstontx.gov",
    url: "https://www.houstontx.gov/council/f/index.html",
  },
  G: {
    member: "Mary Nan Huffman",
    phone: "832-393-3007",
    email: "districtg@houstontx.gov",
    url: "https://www.houstontx.gov/council/g/index.html",
  },
  H: {
    member: "Mario Castillo",
    phone: "832-393-3003",
    email: "districth@houstontx.gov",
    url: "https://www.houstontx.gov/council/h/index.html",
  },
  I: {
    member: "Joaquin Martinez",
    phone: "832-393-3011",
    email: "districti@houstontx.gov",
    url: "https://www.houstontx.gov/council/i/index.html",
  },
  J: {
    member: "Edward Pollard",
    phone: "832-393-3015",
    email: "districtj@houstontx.gov",
    url: "https://www.houstontx.gov/council/j/index.html",
  },
  K: {
    member: "Martha Castex-Tatum",
    phone: "832-393-3016",
    email: "districtk@houstontx.gov",
    url: "https://www.houstontx.gov/council/k/index.html",
  },
});

export const HCAD_BLOCKFACE_OUT_FIELDS =
  "HCAD_NUM,owner,subdivision,address,city,zip,state_class,appr_val,mkt_val,impr_val,land_val,legal_lines,Shape.STArea()";
export const HCAD_BLOCKFACE_FALLBACK_OUT_FIELDS =
  "HCAD_NUM,owner,subdivision,address,city,zip,state_class,appr_val,mkt_val,impr_val,land_val,legal_lines";
export const HCAD_BLOCKFACE_CORE_OUT_FIELDS =
  "HCAD_NUM,address,owner,subdivision,state_class,appr_val";

export const COH_CHAPTER_42_RESOURCES = {
  hubUrl: "https://www.houstontx.gov/planning/Min-Lot_Size-Min_Bldg_Line.html",
  brochurePdfUrl: "https://www.houstontx.gov/planning/Neighborhood/docs_pdfs/MLS_Brochure.pdf",
  smlsBlockAppPdfUrl: "https://www.houstontx.gov/planning/docs_pdfs/MLS-MBL-Application2026/SMLS-Block-NewApp-0821.pdf",
  smlsAreaAppPdfUrl: "https://www.houstontx.gov/planning/docs_pdfs/MLS-MBL-Application2026/SMLS-Area-NewApp-0821.pdf",
  smblBlockAppPdfUrl: "https://www.houstontx.gov/planning/docs_pdfs/MLS-MBL-Application2026/SMBL-Block-NewApp-0821.pdf",
  smlsOrdPdfUrl: "https://www.houstontx.gov/planning/Neighborhood/docs_pdfs/SMLS_Ord.pdf",
  smblOrdPdfUrl: "https://www.houstontx.gov/planning/Neighborhood/docs_pdfs/SMBL_Ord.pdf",
  councilIndexUrl: "https://www.houstontx.gov/council/index.html",
  planningIntakePhone: "(832) 393-6637",
};

export const SCORECARD_FAMILIES = [
  { key: "council_districts", label: "City Council Districts (A-K)", shortLabel: "Council Districts" },
  { key: "historic_districts", label: "COH Historic Districts", shortLabel: "COH Historic" },
  { key: "heritage_districts", label: "COH Heritage Districts", shortLabel: "Heritage" },
  { key: "nrhp_districts", label: "National Register Districts", shortLabel: "NRHP Districts" },
  { key: "potential_historic_districts", label: "Surveyed Potential Districts", shortLabel: "Potential HDs" },
  { key: "super_neighborhoods", label: "Super Neighborhoods (88)", shortLabel: "Super Nbhd" },
  { key: "holc_1937_zones", label: "1937 HOLC Redlining Zones", shortLabel: "1937 HOLC" },
  { key: "neighborhoods", label: "Neighborhoods & Subdivisions", shortLabel: "Neighborhoods" },
  { key: "platted_subdivisions", label: "Historic Platted Additions", shortLabel: "Historic Plats" },
];

export const VALID_SCORECARD_LAYERS = new Set(SCORECARD_FAMILIES.map((f) => f.key));

// ============================================================================
// 1. Domain-Neutral Houston Planar Geometry Primitives (DF-12, DF-6)
// ============================================================================

const WGS84_SEMI_MAJOR_M = 6378137.0;
const WGS84_ECC_SQ = 0.0066943799901413165;
const US_SURVEY_FT_PER_METER = 3937.0 / 1200.0;
const DEG_TO_RAD = Math.PI / 180.0;
export const HOUSTON_DEFAULT_LAT_DEG = 29.7604;
export const HOUSTON_DEFAULT_LNG_DEG = -95.3698;

/**
 * Computes exact WGS84 meridional (north-south) and parallel (east-west) scale factors
 * in US survey feet per degree at a given reference latitude so planar Houston parcel areas
 * match EPSG:2278 Shape.STArea() within < 0.05%.
 */
export function getHoustonSurveyFeetScale(refLatDeg = HOUSTON_DEFAULT_LAT_DEG) {
  const lat = Number.isFinite(Number(refLatDeg)) ? Number(refLatDeg) : HOUSTON_DEFAULT_LAT_DEG;
  const phi = lat * DEG_TO_RAD;
  const sinPhi = Math.sin(phi);
  const cosPhi = Math.cos(phi);
  const wSq = 1.0 - WGS84_ECC_SQ * sinPhi * sinPhi;
  const w = Math.sqrt(wSq);
  const meridionalRadiusM = (WGS84_SEMI_MAJOR_M * (1.0 - WGS84_ECC_SQ)) / (wSq * w);
  const primeVerticalRadiusM = WGS84_SEMI_MAJOR_M / w;
  return {
    ftPerDegLng: primeVerticalRadiusM * cosPhi * DEG_TO_RAD * US_SURVEY_FT_PER_METER,
    ftPerDegLat: meridionalRadiusM * DEG_TO_RAD * US_SURVEY_FT_PER_METER,
    mPerDegLng: primeVerticalRadiusM * cosPhi * DEG_TO_RAD,
    mPerDegLat: meridionalRadiusM * DEG_TO_RAD,
  };
}

function getGeometryReferenceOrigin(geometry) {
  if (!geometry || !geometry.coordinates) return null;
  const t = geometry.type;
  let ring = null;
  if (t === "Polygon" && Array.isArray(geometry.coordinates[0])) {
    ring = geometry.coordinates[0];
  } else if (t === "MultiPolygon" && Array.isArray(geometry.coordinates[0]?.[0])) {
    ring = geometry.coordinates[0][0];
  } else if (t === "Point" && Array.isArray(geometry.coordinates)) {
    const [x, y] = geometry.coordinates;
    if (Number.isFinite(x) && Number.isFinite(y)) return { refLng: x, refLat: y };
  }
  if (!ring || ring.length === 0) return null;
  let sx = 0;
  let sy = 0;
  let cnt = 0;
  for (const pt of ring) {
    if (Array.isArray(pt) && Number.isFinite(pt[0]) && Number.isFinite(pt[1])) {
      sx += pt[0];
      sy += pt[1];
      cnt += 1;
    }
  }
  return cnt > 0 ? { refLng: sx / cnt, refLat: sy / cnt } : null;
}

/**
 * Computes the signed planar area (in US survey sq ft) of a single linear ring.
 */
export function computeRingSignedAreaSqft(ring, refLng, refLat) {
  if (!Array.isArray(ring) || ring.length < 3) return 0;
  const originLng = Number.isFinite(refLng) ? refLng : Number(ring[0]?.[0]) || HOUSTON_DEFAULT_LNG_DEG;
  const originLat = Number.isFinite(refLat) ? refLat : Number(ring[0]?.[1]) || HOUSTON_DEFAULT_LAT_DEG;
  const { ftPerDegLng, ftPerDegLat } = getHoustonSurveyFeetScale(originLat);
  let area2 = 0;
  const n = ring.length;
  for (let i = 0; i < n; i += 1) {
    const p1 = ring[i];
    const p2 = ring[(i + 1) % n];
    if (!p1 || !p2) continue;
    const x1 = (Number(p1[0]) - originLng) * ftPerDegLng;
    const y1 = (Number(p1[1]) - originLat) * ftPerDegLat;
    const x2 = (Number(p2[0]) - originLng) * ftPerDegLng;
    const y2 = (Number(p2[1]) - originLat) * ftPerDegLat;
    if (!Number.isFinite(x1 + y1 + x2 + y2)) continue;
    area2 += x1 * y2 - x2 * y1;
  }
  return area2 * 0.5;
}

/**
 * Computes net hole-subtracted planar area in Houston US survey square feet across
 * GeoJSON Polygon and MultiPolygon geometries.
 */
export function computePolygonAreaSqftPlanar(geometry) {
  if (!geometry || !geometry.type || !Array.isArray(geometry.coordinates)) return 0;
  const origin = getGeometryReferenceOrigin(geometry);
  if (!origin) return 0;
  const { refLng, refLat } = origin;

  const polygonArea = (rings) => {
    if (!Array.isArray(rings) || rings.length === 0) return 0;
    let net = Math.abs(computeRingSignedAreaSqft(rings[0], refLng, refLat));
    for (let h = 1; h < rings.length; h += 1) {
      net -= Math.abs(computeRingSignedAreaSqft(rings[h], refLng, refLat));
    }
    return Math.max(0, net);
  };

  if (geometry.type === "Polygon") {
    return polygonArea(geometry.coordinates);
  }
  if (geometry.type === "MultiPolygon") {
    let total = 0;
    for (const poly of geometry.coordinates) {
      total += polygonArea(poly);
    }
    return total;
  }
  return 0;
}

/**
 * Resolves a parcel's land area in US survey square feet, honoring a verified positive
 * ArcGIS Shape.STArea() hint when supplied and otherwise projecting geometry cleanly.
 */
export function computeHoustonParcelAreaSqFt(geometry, serverAreaHint = null) {
  if (serverAreaHint !== null && serverAreaHint !== undefined && serverAreaHint !== "") {
    const num = Number(serverAreaHint);
    if (Number.isFinite(num) && num > 0) {
      return num;
    }
  }
  return computePolygonAreaSqftPlanar(geometry);
}

export function computeGeometryBbox(geometry) {
  if (!geometry || !geometry.coordinates) return null;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  const visitRing = (ring) => {
    if (!Array.isArray(ring)) return;
    for (const pt of ring) {
      if (!Array.isArray(pt)) continue;
      const x = Number(pt[0]);
      const y = Number(pt[1]);
      if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
      if (x < minX) minX = x;
      if (y < minY) minY = y;
      if (x > maxX) maxX = x;
      if (y > maxY) maxY = y;
    }
  };
  if (geometry.type === "Point") {
    const x = Number(geometry.coordinates[0]);
    const y = Number(geometry.coordinates[1]);
    if (Number.isFinite(x) && Number.isFinite(y)) return [x, y, x, y];
    return null;
  }
  if (geometry.type === "Polygon") {
    for (const r of geometry.coordinates) visitRing(r);
  } else if (geometry.type === "MultiPolygon") {
    for (const poly of geometry.coordinates) {
      if (Array.isArray(poly)) for (const r of poly) visitRing(r);
    }
  }
  return Number.isFinite(minX) ? [minX, minY, maxX, maxY] : null;
}

export function pointInRing(lng, lat, ring) {
  if (!Array.isArray(ring) || ring.length < 3) return false;
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const xi = Number(ring[i]?.[0]);
    const yi = Number(ring[i]?.[1]);
    const xj = Number(ring[j]?.[0]);
    const yj = Number(ring[j]?.[1]);
    const intersect =
      yi > lat !== yj > lat && lng < ((xj - xi) * (lat - yi)) / (yj - yi) + xi;
    if (intersect) inside = !inside;
  }
  return inside;
}

/**
 * Hole-respecting GeoJSON point-in-polygon check across Polygon and MultiPolygon.
 */
export function pointInGeometry(lng, lat, geometry, precomputedBbox = null) {
  const x = Number(lng);
  const y = Number(lat);
  if (!Number.isFinite(x) || !Number.isFinite(y) || !geometry) return false;
  const bb = precomputedBbox || computeGeometryBbox(geometry);
  if (bb && (x < bb[0] || x > bb[2] || y < bb[1] || y > bb[3])) return false;

  const inPolyRings = (rings) => {
    if (!Array.isArray(rings) || rings.length === 0) return false;
    if (!pointInRing(x, y, rings[0])) return false;
    for (let h = 1; h < rings.length; h += 1) {
      if (pointInRing(x, y, rings[h])) return false;
    }
    return true;
  };

  if (geometry.type === "Polygon") return inPolyRings(geometry.coordinates);
  if (geometry.type === "MultiPolygon") {
    for (const poly of geometry.coordinates) {
      if (inPolyRings(poly)) return true;
    }
  }
  return false;
}

/**
 * Computes an area-weighted interior centroid (or vertex fallback strictly inside the outer ring
 * and outside holes when concave shapes move the centroid into a notch/hole).
 */
export function computeGeometryRepresentativePoint(geometry) {
  if (!geometry || !geometry.coordinates) return null;
  if (geometry.type === "Point") {
    const [x, y] = geometry.coordinates;
    return Number.isFinite(x) && Number.isFinite(y) ? [x, y] : null;
  }
  const polys =
    geometry.type === "Polygon"
      ? [geometry.coordinates]
      : geometry.type === "MultiPolygon"
      ? geometry.coordinates
      : [];
  let bestPoly = null;
  let bestArea = -1;
  for (const poly of polys) {
    if (!Array.isArray(poly) || !Array.isArray(poly[0]) || poly[0].length < 3) continue;
    const a = computePolygonAreaSqftPlanar({ type: "Polygon", coordinates: poly });
    if (a > bestArea) {
      bestArea = a;
      bestPoly = poly;
    }
  }
  if (!bestPoly) return null;
  const ring = bestPoly[0];
  let x0 = Number(ring[0][0]);
  let y0 = Number(ring[0][1]);
  let twiceA = 0;
  let cxAcc = 0;
  let cyAcc = 0;
  for (let i = 0; i < ring.length - 1; i += 1) {
    const x1 = Number(ring[i][0]) - x0;
    const y1 = Number(ring[i][1]) - y0;
    const x2 = Number(ring[i + 1][0]) - x0;
    const y2 = Number(ring[i + 1][1]) - y0;
    const cross = x1 * y2 - x2 * y1;
    twiceA += cross;
    cxAcc += (x1 + x2) * cross;
    cyAcc += (y1 + y2) * cross;
  }
  if (Math.abs(twiceA) > 1e-14) {
    const cx = x0 + cxAcc / (3 * twiceA);
    const cy = y0 + cyAcc / (3 * twiceA);
    if (pointInGeometry(cx, cy, geometry)) return [cx, cy];
  }
  // Concave / doughnut fallback: midpoint between adjacent vertices nudged slightly inward
  let sx = 0;
  let sy = 0;
  let cnt = 0;
  for (const pt of ring) {
    if (Array.isArray(pt) && Number.isFinite(pt[0]) && Number.isFinite(pt[1])) {
      sx += pt[0];
      sy += pt[1];
      cnt += 1;
    }
  }
  const meanPt = cnt > 0 ? [sx / cnt, sy / cnt] : null;
  if (meanPt && pointInGeometry(meanPt[0], meanPt[1], geometry)) return meanPt;
  for (let i = 0; i < ring.length - 1; i += 1) {
    for (let j = i + 2; j < ring.length - 1; j += 1) {
      const mx = (Number(ring[i][0]) + Number(ring[j][0])) * 0.5;
      const my = (Number(ring[i][1]) + Number(ring[j][1])) * 0.5;
      if (pointInGeometry(mx, my, geometry)) return [mx, my];
    }
  }
  return meanPt;
}

export function pointToSegmentDistFt(px, py, ax, ay, bx, by) {
  const dx = bx - ax;
  const dy = by - ay;
  const lenSq = dx * dx + dy * dy;
  if (lenSq <= 1e-12) return Math.hypot(px - ax, py - ay);
  const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / lenSq));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

/**
 * Returns undirected axial bearing in [0, 180) degrees (0 = East-West axis, 90 = North-South axis).
 */
export function segmentBearingDeg(ax, ay, bx, by) {
  let deg = (Math.atan2(by - ay, bx - ax) * 180.0) / Math.PI;
  while (deg < 0) deg += 180.0;
  while (deg >= 180.0) deg -= 180.0;
  return deg;
}

export function axialAngleDiffDeg(degA, degB) {
  const d = Math.abs((degA % 180) - (degB % 180));
  return Math.min(d, 180 - d);
}

function segmentsIntersect2D(ax, ay, bx, by, cx, cy, dx, dy) {
  const orient = (px, py, qx, qy, rx, ry) => (qy - py) * (rx - qx) - (qx - px) * (ry - qy);
  const o1 = orient(ax, ay, bx, by, cx, cy);
  const o2 = orient(ax, ay, bx, by, dx, dy);
  const o3 = orient(cx, cy, dx, dy, ax, ay);
  const o4 = orient(cx, cy, dx, dy, bx, by);
  if (((o1 > 0 && o2 < 0) || (o1 < 0 && o2 > 0)) && ((o3 > 0 && o4 < 0) || (o3 < 0 && o4 > 0))) {
    return true;
  }
  return false;
}

export function segmentToSegmentDistFt(ax, ay, bx, by, cx, cy, dx, dy) {
  if (segmentsIntersect2D(ax, ay, bx, by, cx, cy, dx, dy)) return 0;
  return Math.min(
    pointToSegmentDistFt(ax, ay, cx, cy, dx, dy),
    pointToSegmentDistFt(bx, by, cx, cy, dx, dy),
    pointToSegmentDistFt(cx, cy, ax, ay, bx, by),
    pointToSegmentDistFt(dx, dy, ax, ay, bx, by)
  );
}

function extractOuterRings(geometry) {
  if (!geometry || !Array.isArray(geometry.coordinates)) return [];
  if (geometry.type === "Polygon" && Array.isArray(geometry.coordinates[0])) {
    return [geometry.coordinates[0]];
  }
  if (geometry.type === "MultiPolygon") {
    return geometry.coordinates.filter((p) => Array.isArray(p?.[0])).map((p) => p[0]);
  }
  return [];
}

/**
 * Computes minimum edge-to-edge separation distance in meters between two GeoJSON polygons.
 */
export function minPolygonGapMeters(geomA, geomB) {
  const ringsA = extractOuterRings(geomA);
  const ringsB = extractOuterRings(geomB);
  if (ringsA.length === 0 || ringsB.length === 0) return Infinity;
  const origin = getGeometryReferenceOrigin(geomA) || getGeometryReferenceOrigin(geomB);
  if (!origin) return Infinity;
  const { refLng, refLat } = origin;
  const { ftPerDegLng, ftPerDegLat } = getHoustonSurveyFeetScale(refLat);
  const toFt = (pt) => [(pt[0] - refLng) * ftPerDegLng, (pt[1] - refLat) * ftPerDegLat];

  for (const rB of ringsB) {
    if (rB[0] && pointInGeometry(rB[0][0], rB[0][1], geomA)) return 0;
  }
  for (const rA of ringsA) {
    if (rA[0] && pointInGeometry(rA[0][0], rA[0][1], geomB)) return 0;
  }

  let minFt = Infinity;
  for (const rA of ringsA) {
    const ptsA = rA.map(toFt);
    for (const rB of ringsB) {
      const ptsB = rB.map(toFt);
      for (let i = 0; i < ptsA.length - 1; i += 1) {
        const [ax, ay] = ptsA[i];
        const [bx, by] = ptsA[i + 1];
        for (let j = 0; j < ptsB.length - 1; j += 1) {
          const [cx, cy] = ptsB[j];
          const [dx, dy] = ptsB[j + 1];
          const d = segmentToSegmentDistFt(ax, ay, bx, by, cx, cy, dx, dy);
          if (d < minFt) {
            minFt = d;
            if (minFt <= 0.01) return 0;
          }
        }
      }
    }
  }
  return minFt / US_SURVEY_FT_PER_METER;
}

// ============================================================================
// 2. Chapter 42 Statutory Sunset Lifecycle Resolver (DF-1)
// ============================================================================

function parseIsoOrFlexibleDate(rawVal) {
  if (rawVal === null || rawVal === undefined || rawVal === "") {
    return { iso: "", year: 0, exact: false };
  }
  if (typeof rawVal === "number" && Number.isFinite(rawVal) && rawVal > 1e11) {
    const d = new Date(rawVal);
    if (!Number.isNaN(d.getTime())) {
      const iso = d.toISOString().slice(0, 10);
      const yr = Number(iso.slice(0, 4));
      if (yr >= 1980 && yr <= 2100) return { iso, year: yr, exact: true };
    }
  }
  const s = String(rawVal).trim();
  if (!s || /^(none|null|n\/a|unknown)$/i.test(s)) return { iso: "", year: 0, exact: false };
  const isoM = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (isoM) {
    const yr = Number(isoM[1]);
    if (yr >= 1980 && yr <= 2100) {
      return { iso: `${isoM[1]}-${isoM[2]}-${isoM[3]}`, year: yr, exact: true };
    }
  }
  const usM = s.match(/^(\d{1,2})[/-](\d{1,2})[/-](\d{4})/);
  if (usM) {
    const yr = Number(usM[3]);
    const mo = String(Number(usM[1])).padStart(2, "0");
    const dy = String(Number(usM[2])).padStart(2, "0");
    if (yr >= 1980 && yr <= 2100) return { iso: `${yr}-${mo}-${dy}`, year: yr, exact: true };
  }
  const yrOnly = s.match(/^(\d{4})$/);
  if (yrOnly) {
    const yr = Number(yrOnly[1]);
    if (yr >= 1980 && yr <= 2100) return { iso: `${yr}-01-01`, year: yr, exact: false };
  }
  return { iso: "", year: 0, exact: false };
}

/**
 * Resolves the statutory enactment, term (20 vs. 40 years under Ord. 2015-639), and sunset year
 * for any City of Houston Chapter 42 SMLS / SMBL record (mirrored with Python schema.py, DF-1).
 */
export function resolveCh42OrdinanceLifecycle(props = {}, refYearArg = 2026) {
  const p = props && typeof props === "object" ? props : {};
  let refYear = Number(refYearArg);
  if (p.referenceDate) {
    const rd = parseIsoOrFlexibleDate(p.referenceDate);
    if (rd.year >= 1980) refYear = rd.year;
  } else if (Number.isFinite(Number(p.ref_year)) && Number(p.ref_year) >= 1980) {
    refYear = Number(p.ref_year);
  }
  if (!Number.isFinite(refYear) || refYear < 1980) refYear = 2026;

  const ptype = String(p.protectionType ?? p.protection_type ?? "smls").trim().toLowerCase();
  const statusStr = String(p.status ?? p.STATUS ?? "Approved").trim().toLowerCase();
  const ordRaw = String(
    p.ordinanceNo ?? p.ordinance ?? p.ORDINANCE_ ?? p.ORDINANCE ?? ""
  ).trim();

  if (ptype === "conservation") {
    return {
      effective_date: "",
      effective_year: 0,
      effectiveYear: 0,
      expiration_date: "",
      expirationDateIso: "",
      expiration_year: 0,
      expirationYear: 0,
      expiration_label: "Permanent (Chapter 33)",
      statutory_term_years: 0,
      statutoryTermYears: 0,
      is_estimated_sunset: false,
      isDerivedFromOrdinanceNumber: false,
      sunset_status: "permanent",
      renewalStatus: "permanent",
    };
  }

  if (
    statusStr.includes("pend") ||
    statusStr.includes("review") ||
    statusStr.includes("applied") ||
    statusStr.includes("draft")
  ) {
    return {
      effective_date: "",
      effective_year: 0,
      effectiveYear: 0,
      expiration_date: "",
      expirationDateIso: "",
      expiration_year: 0,
      expirationYear: 0,
      expiration_label: "Pending review",
      statutory_term_years: 40,
      statutoryTermYears: 40,
      is_estimated_sunset: false,
      isDerivedFromOrdinanceNumber: false,
      sunset_status: "pending",
      renewalStatus: "pending",
    };
  }

  if (
    statusStr.includes("denied") ||
    statusStr.includes("withdraw") ||
    statusStr.includes("rescind") ||
    statusStr.includes("reject")
  ) {
    return {
      effective_date: "",
      effective_year: 0,
      effectiveYear: 0,
      expiration_date: "",
      expirationDateIso: "",
      expiration_year: 0,
      expirationYear: 0,
      expiration_label: "Inactive",
      statutory_term_years: 0,
      statutoryTermYears: 0,
      is_estimated_sunset: false,
      isDerivedFromOrdinanceNumber: false,
      sunset_status: "inactive",
      renewalStatus: "inactive",
    };
  }

  // Prefer original raw gis_expiration_date when hydrated records are re-evaluated
  const rawExpInput =
    "gis_expiration_date" in p
      ? p.gis_expiration_date
      : p.is_estimated_sunset
      ? ""
      : p.expirationDate ?? p.expiration_date ?? p.Expiration_Date ?? "";
  const expParsed = parseIsoOrFlexibleDate(rawExpInput);
  const effParsed = parseIsoOrFlexibleDate(
    p.effectiveDate ?? p.effective_date ?? p.Effective_Date ?? p.startDate ?? p.START_DATE ?? ""
  );

  let ordYear = 0;
  let ordNum = 0;
  const ordM = ordRaw.match(/\b((?:19|20)\d{2})\s*[-/]\s*0*(\d{1,5})\b/);
  if (ordM) {
    const oy = Number(ordM[1]);
    if (oy >= 1995 && oy <= 2050) {
      ordYear = oy;
      ordNum = Number(ordM[2]);
    }
  }

  const enactmentYr = effParsed.year || ordYear;
  let termYears = 20;
  if (effParsed.exact && effParsed.iso) {
    termYears = effParsed.iso >= ORD_2015_639_CUTOFF_ISO ? 40 : 20;
  } else if (ordYear > 2015) {
    termYears = 40;
  } else if (ordYear === 2015) {
    termYears = ordNum > 0 ? (ordNum >= ORD_2015_639_CUTOFF_NUM ? 40 : 20) : 40;
  } else if (effParsed.year > 2015) {
    termYears = 40;
  } else if (effParsed.year === 2015) {
    termYears = 40;
  } else if (expParsed.year && enactmentYr && expParsed.year - enactmentYr >= 30) {
    termYears = 40;
  }

  let finalExpIso = expParsed.iso;
  let finalExpYr = expParsed.year;
  let isEstimated = false;
  let derivedFromOrd = false;

  if (!finalExpYr && enactmentYr >= 1995) {
    isEstimated = true;
    finalExpYr = enactmentYr + termYears;
    if (
      effParsed.exact &&
      effParsed.iso &&
      /^\d{4}-\d{2}-\d{2}$/.test(effParsed.iso) &&
      (p.Effective_Date || p.effectiveDate || p.effective_date || p.START_DATE || p.startDate)
    ) {
      const md = effParsed.iso.slice(5, 10) === "02-29" ? "02-28" : effParsed.iso.slice(5, 10);
      finalExpIso = `${finalExpYr}-${md}`;
    } else {
      derivedFromOrd = true;
      finalExpIso = `${finalExpYr}-12-31`;
    }
  }

  let sunsetStatus = "unknown";
  if (finalExpYr) {
    if (finalExpYr < refYear) {
      sunsetStatus = "expired";
    } else if (finalExpYr <= refYear + 5) {
      sunsetStatus = "expiring_soon";
    } else {
      sunsetStatus = "active";
    }
  } else if (statusStr === "expired") {
    sunsetStatus = "expired";
  }

  let expLabel = "Active (no sunset listed)";
  if (finalExpYr) {
    expLabel = isEstimated
      ? `Est. ${finalExpYr} (${termYears}-yr statutory sunset)`
      : finalExpIso || String(finalExpYr);
  }

  return {
    effective_date: effParsed.iso,
    effective_year: enactmentYr,
    effectiveYear: enactmentYr,
    expiration_date: finalExpIso,
    expirationDateIso: finalExpIso,
    expiration_year: finalExpYr,
    expirationYear: finalExpYr,
    expiration_label: expLabel,
    statutory_term_years: termYears,
    statutoryTermYears: termYears,
    is_estimated_sunset: isEstimated,
    isDerivedFromOrdinanceNumber: derivedFromOrd,
    sunset_status: sunsetStatus,
    renewalStatus: sunsetStatus,
  };
}

// ============================================================================
// 3. Scorecard Wire Decoder & Lazy Dataset Loader (DF-13)
// ============================================================================

function clampPct(numerator, denominator) {
  const num = Number(numerator) || 0;
  const den = Number(denominator) || 0;
  if (den <= 0 || num <= 0) return 0;
  return Math.max(0, Math.min(100, Number(((num / den) * 100).toFixed(1))));
}

/**
 * Single anti-corruption decoder translating compact wire fields in neighborhood_scorecards.json
 * into explicit, self-documenting domain objects (DF-13).
 */
export function decodeScorecardRecord(raw, meta = {}) {
  if (!raw || typeof raw !== "object") return null;
  const decadeBins =
    Array.isArray(meta?.decade_bins) && meta.decade_bins.length > 0
      ? meta.decade_bins
      : [
          1830, 1840, 1850, 1860, 1870, 1880, 1890, 1900, 1910, 1920,
          1930, 1940, 1950, 1960, 1970, 1980, 1990, 2000, 2010, 2020,
        ];
  const demoYears =
    Array.isArray(meta?.demo_years) && meta.demo_years.length > 0
      ? meta.demo_years
      : [2020, 2021, 2022, 2023, 2024, 2025, 2026];

  const buildingCount = Number(raw["bc"]) || 0;
  const pre1920Count = Number(raw["p20"]) || 0;
  const pre1940Count = Number(raw["p40"]) || 0;
  const pre1960Count = Number(raw["p60"]) || 0;
  const pre1975Count = Number(raw["p75"]) || 0;
  const post2020InfillCount = Number(raw["n20"]) || 0;
  const cohDistrictProtectedBuildings = Math.min(buildingCount, Number(raw["hdp"]) || 0);

  const parcelCount = Number(raw["pc"]) || 0;
  const residentialParcelCount = Math.min(parcelCount, Number(raw["rpc"]) || 0);
  const singleFamilyParcelCount = Math.min(residentialParcelCount, Number(raw["sfc"]) || 0);
  const ownerOccupiedCount = Math.min(residentialParcelCount, Number(raw["occ"]) || 0);
  const familyTrustCount = Math.min(residentialParcelCount, Number(raw["ftc"]) || 0);
  const corporateLlcCount = Math.min(residentialParcelCount, Number(raw["llc"]) || 0);
  const absenteeIndividualCount = Math.min(residentialParcelCount, Number(raw["abs"]) || 0);

  const ch42ProtectedCount = Math.min(residentialParcelCount, Number(raw["chp"]) || 0);
  const smlsProtectedCount = Math.min(residentialParcelCount, Number(raw["slp"]) || 0);
  const smblProtectedCount = Math.min(residentialParcelCount, Number(raw["sbp"]) || 0);
  const expiringCh42ParcelCount = Math.min(ch42ProtectedCount, Number(raw["chx"]) || 0);

  const rawDh = Array.isArray(raw["dh"]) ? raw["dh"] : [];
  const decadeHistogram = decadeBins.map((decade, idx) => ({
    decade,
    count: Number(rawDh[idx]) || 0,
  }));

  const rawDm = Array.isArray(raw["dm"]) ? raw["dm"] : [];
  const demolitionsByYear = demoYears.map((year, idx) => ({
    year,
    count: Number(rawDm[idx]) || 0,
    isYtd: year === 2026 && meta?.demo_2026_is_ytd !== false,
  }));

  const rawExo = Array.isArray(raw["exo"]) ? raw["exo"] : [];
  const expiringOrdinances = rawExo.map((item) => ({
    ordinance: String(item?.["o"] || "Unnumbered"),
    protectionType: String(item?.["t"] || "SMLS"),
    expirationYear: Number(item?.["y"]) || 0,
    isEstimatedSunset: Boolean(item?.["est"]),
  }));

  const layer = String(raw["ly"] || "");
  const id = String(raw["id"] || "");

  return {
    key: `${layer}:${id}`,
    id,
    layer,
    name: String(raw["nm"] || ""),
    typeLabel: String(raw["tp"] || ""),
    centerLng: Number(raw["cx"]) || 0,
    centerLat: Number(raw["cy"]) || 0,
    bbox: Array.isArray(raw["bb"]) ? raw["bb"].map(Number) : null,
    areaAcres: Number(raw["ac"]) || 0,
    buildingCount,
    earliestYear: Number(raw["ey"]) || 0,
    medianYear: Number(raw["my"]) || 0,
    pre1920Count,
    pre1940Count,
    pre1960Count,
    pre1975Count,
    post2020InfillCount,
    pre1940Pct: clampPct(pre1940Count, buildingCount),
    pre1960Pct: clampPct(pre1960Count, buildingCount),
    pre1975Pct: clampPct(pre1975Count, buildingCount),
    decadeHistogram,
    landmarkCount: Number(raw["lm"]) || 0,
    protectedLandmarkCount: Number(raw["plm"]) || 0,
    waiverLandmarkCount: Number(raw["vlm"]) || 0,
    goodBrickCount: Number(raw["gb"]) || 0,
    nrhpPropertyCount: Number(raw["nrp"]) || 0,
    lostHoustonCount: Number(raw["lhc"]) || 0,
    cohDistrictProtectedBuildings,
    cohChapter33Pct: clampPct(cohDistrictProtectedBuildings, buildingCount),
    unprotectedPre1940Estimate:
      cohDistrictProtectedBuildings >= buildingCount ? 0 : pre1940Count,
    parcelCount,
    residentialParcelCount,
    singleFamilyParcelCount,
    sfrSharePct: clampPct(singleFamilyParcelCount, residentialParcelCount),
    ownerOccupiedCount,
    ownerOccupiedPct: clampPct(ownerOccupiedCount, residentialParcelCount),
    familyTrustCount,
    familyTrustPct: clampPct(familyTrustCount, residentialParcelCount),
    corporateLlcCount,
    corporateLlcPct: clampPct(corporateLlcCount, residentialParcelCount),
    absenteeIndividualCount,
    absenteeIndividualPct: clampPct(absenteeIndividualCount, residentialParcelCount),
    ch42ProtectedCount,
    ch42ProtectedPct: clampPct(ch42ProtectedCount, residentialParcelCount),
    smlsProtectedCount,
    smlsProtectedPct: clampPct(smlsProtectedCount, residentialParcelCount),
    smblProtectedCount,
    smblProtectedPct: clampPct(smblProtectedCount, residentialParcelCount),
    expiringCh42ParcelCount,
    smlsOrdinanceCount: Number(raw["slo"]) || 0,
    smblOrdinanceCount: Number(raw["sbo"]) || 0,
    conservationDistrictCount: Number(raw["cdo"]) || 0,
    expiringOrdinances,
    demolitionsByYear,
    demolitionsTotal2020To2026: Number(raw["dmt"]) || 0,
    demolitionsPre1940Count: Number(raw["dmp40"]) || 0,
    superNeighborhood: String(raw["sn"] || ""),
    councilDistricts: String(raw["cd"] || ""),
    civicClub: String(raw["cc"] || ""),
    councilMemberMeta: raw["cm"] ? { ...raw["cm"] } : null,
    holcMeta: raw["hl"] ? { ...raw["hl"] } : null,
    platMeta: raw["pl"] ? { ...raw["pl"] } : null,
    alreadyDesignatedCohHd: Boolean(raw["ad"]),
  };
}

let _scorecardsCache = null;
let _scorecardsInFlight = null;

export function resetNeighborhoodScorecardsCache() {
  _scorecardsCache = null;
  _scorecardsInFlight = null;
}

/**
 * Lazily loads and decodes app/public/data/neighborhood_scorecards.json on first demand,
 * keeping initial map boot latency at 0 KB added payload.
 */
export async function loadNeighborhoodScorecards({
  fetchImpl = typeof fetch === "function" ? fetch.bind(globalThis) : null,
  url = NEIGHBORHOOD_SCORECARDS_URL,
  forceReload = false,
} = {}) {
  if (!forceReload && _scorecardsCache) return _scorecardsCache;
  if (!forceReload && _scorecardsInFlight) return _scorecardsInFlight;
  if (typeof fetchImpl !== "function") {
    throw new Error("Fetch implementation unavailable for loadNeighborhoodScorecards.");
  }

  const pending = (async () => {
    const resp = await fetchImpl(url);
    if (!resp || !resp.ok) {
      throw new Error(`Failed to fetch neighborhood scorecards (HTTP ${resp ? resp.status : "error"}).`);
    }
    const payload = await resp.json();
    const rawList = Array.isArray(payload?.scorecards) ? payload.scorecards : [];
    const byKey = new Map();
    const byLayer = new Map();
    for (const fam of SCORECARD_FAMILIES) byLayer.set(fam.key, []);

    const records = [];
    for (const raw of rawList) {
      const rec = decodeScorecardRecord(raw, payload);
      if (!rec || !rec.id || !rec.layer) continue;
      records.push(rec);
      byKey.set(rec.key, rec);
      if (!byLayer.has(rec.layer)) byLayer.set(rec.layer, []);
      byLayer.get(rec.layer).push(rec);
    }

    const result = {
      schemaVersion: String(payload?.schema_version || "1.0"),
      generatedAt: String(payload?.generated_at || ""),
      councilTermExpires: String(payload?.council_term_expires || "2028-01"),
      minSubdivisionStandingStructures: Number(payload?.min_subdivision_standing_structures) || 10,
      decadeBins: Array.isArray(payload?.decade_bins) ? payload.decade_bins : [],
      demoYears: Array.isArray(payload?.demo_years) ? payload.demo_years : [],
      demo2026IsYtd: payload?.demo_2026_is_ytd !== false,
      familyCounts: payload?.family_counts || {},
      councilRoster: payload?.council_roster || {},
      records,
      byKey,
      byLayer,
    };
    if (!forceReload) _scorecardsCache = result;
    return result;
  })();

  if (!forceReload) {
    const tracked = pending.finally(() => {
      if (_scorecardsInFlight === tracked) _scorecardsInFlight = null;
    });
    _scorecardsInFlight = tracked;
    return tracked;
  }
  return pending;
}

// ============================================================================
// 4. Live Multi-Feed Boundary Threat Joiner & Deduplicator (DF-10)
// ============================================================================

function parseValidHarrisCoord(rawLng, rawLat) {
  if (rawLng === null || rawLng === undefined || rawLat === null || rawLat === undefined) {
    return null;
  }
  const sLng = String(rawLng).trim();
  const sLat = String(rawLat).trim();
  if (!sLng || !sLat || sLng === "null" || sLat === "null") return null;
  const lng = Number(sLng);
  const lat = Number(sLat);
  if (!Number.isFinite(lng) || !Number.isFinite(lat)) return null;
  if (lng < -96.8 || lng > -94.0 || lat < 28.5 || lat > 31.0) return null;
  return [lng, lat];
}

export function normalizeThreatAddressKey(rawAddr) {
  const parsed = parseStreetAddressParts(rawAddr);
  if (!parsed || !parsed.streetKey) return "";
  if (parsed.houseNumber !== null && !parsed.isZeroAddress) {
    const sfx = parsed.houseSuffix ? ` ${parsed.houseSuffix}` : "";
    return `${parsed.houseNumber}${sfx} ${parsed.streetKey}`.trim();
  }
  return parsed.streetKey;
}

function normalizeNameToken(s) {
  return String(s || "")
    .toLowerCase()
    .replace(/\b(historic\s+district|super\s+neighborhood|addition|civic\s+association)\b/g, "")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

const TERMINAL_WATCHLIST_STATUS_RE =
  /^(saved|lost|archived|removed|closed|resolved|dismissed)/i;

/**
 * Joins active threats across Permit Alerts, Biweekly Plat Agenda, 15-Point Watchlist,
 * and HAHC COA Tracker against a GeoJSON boundary polygon while enforcing null-coordinate
 * guards, tombstone/Tier-3 filtering, and cross-feed 13-digit HCAD deduplication (DF-10).
 */
export function computeLiveBoundaryThreats(boundaryInput, liveDatasets = {}) {
  const boundaryFeat =
    boundaryInput?.type === "Feature"
      ? boundaryInput
      : boundaryInput?.type && boundaryInput?.coordinates
      ? { type: "Feature", geometry: boundaryInput, properties: {} }
      : null;
  const geom = boundaryFeat?.geometry || null;
  const bProps = boundaryFeat?.properties || {};
  const bLayer = String(bProps._overlayKey || bProps.overlay_layer || liveDatasets.boundaryLayer || "");
  const bNameNorm = normalizeNameToken(bProps.name || liveDatasets.boundaryName || "");
  const bbox = geom ? computeGeometryBbox(geom) : null;

  const mergedByKey = new Map();
  const sourceCounts = { permits: 0, plats: 0, watchlist: 0, hahc: 0 };

  const upsertThreat = ({
    source,
    badge,
    hcadNum,
    buildingId = "",
    title = "",
    address = "",
    yearBuilt = 0,
    summary = "",
    date = "",
    url = "",
    lng = null,
    lat = null,
    severityRank = 2,
  }) => {
    const cleanHcad = normalizeHcad13(hcadNum);
    const addrKey = normalizeThreatAddressKey(address);
    const dedupKey = cleanHcad
      ? `hcad:${cleanHcad}`
      : addrKey
      ? `addr:${addrKey}`
      : `title:${source}:${String(title || summary).toUpperCase().trim()}`;

    let rec = mergedByKey.get(dedupKey);
    if (!rec) {
      rec = {
        key: dedupKey,
        hcadNum: cleanHcad,
        buildingId: String(buildingId || ""),
        title: String(title || address || "Tracked Property"),
        address: String(address || ""),
        yearBuilt: Number(yearBuilt) >= 1836 ? Number(yearBuilt) : 0,
        lng,
        lat,
        severityRank,
        latestDate: String(date || ""),
        sources: [],
        badges: [],
        details: [],
      };
      mergedByKey.set(dedupKey, rec);
    } else {
      if (!rec.hcadNum && cleanHcad) rec.hcadNum = cleanHcad;
      if (!rec.buildingId && buildingId) rec.buildingId = String(buildingId);
      if (!rec.address && address) rec.address = String(address);
      if (rec.title === rec.address && title && title !== address) rec.title = String(title);
      const yr = Number(yearBuilt) || 0;
      if (yr >= 1836 && (rec.yearBuilt === 0 || yr < rec.yearBuilt)) rec.yearBuilt = yr;
      if (rec.lng == null && lng != null) {
        rec.lng = lng;
        rec.lat = lat;
      }
      if (severityRank < rec.severityRank) rec.severityRank = severityRank;
      if (date && (!rec.latestDate || String(date) > rec.latestDate)) {
        rec.latestDate = String(date);
      }
    }
    if (!rec.sources.includes(source)) {
      rec.sources.push(source);
      sourceCounts[source] = (sourceCounts[source] || 0) + 1;
    }
    if (badge && !rec.badges.includes(badge)) rec.badges.push(badge);
    if (summary) rec.details.push({ source, badge, summary, date: String(date || ""), url: String(url || "") });
  };

  // 1. Active Demolition & Sewer Disconnection Permit Alerts
  const permitRoot = liveDatasets.activePermitAlerts || liveDatasets.permitAlertsData || null;
  const permitItems = Array.isArray(permitRoot)
    ? permitRoot
    : permitRoot?.alerts || permitRoot?.features || permitRoot?.items || [];
  for (const raw of permitItems) {
    const p = raw?.type === "Feature" ? raw.properties || {} : raw || {};
    const gCoord = raw?.geometry?.type === "Point" ? raw.geometry.coordinates : null;
    const coord = parseValidHarrisCoord(p.lng ?? p.lon ?? gCoord?.[0], p.lat ?? gCoord?.[1]);
    if (!coord || !geom || !pointInGeometry(coord[0], coord[1], geom, bbox)) continue;
    upsertThreat({
      source: "permits",
      badge: String(p.badge || p.severity_label || "Demolition / Sewer Permit Alert"),
      hcadNum: p.hcad_num || p.HCAD_NUM,
      buildingId: p.building_id || "",
      title: p.title || p.building_name || p.landmark_name || p.address,
      address: p.address || "",
      yearBuilt: p.year_built,
      summary: p.headline || p.severity_label || "Active COH demolition or Texas DSHS asbestos alert",
      date: p.latest_filing_date || "",
      lng: coord[0],
      lat: coord[1],
      severityRank: p.severity === "critical" ? 0 : 1,
    });
  }

  // 2. Planning Commission Plat & Variance Agenda Monitor
  const platRoot = liveDatasets.platAgendaMonitor || liveDatasets.platWatchData || null;
  const platReviews = (platRoot && typeof platRoot.reviews === "object" && platRoot.reviews) || {};
  const platItems = Array.isArray(platRoot) ? platRoot : platRoot?.items || platRoot?.features || [];
  for (const raw of platItems) {
    const it = raw?.type === "Feature" ? raw.properties || {} : raw || {};
    if (it?._c?.group === "out") continue;
    const appKey = String(it.app ?? it.appNo ?? "").trim();
    const rev = appKey ? platReviews[appKey] : null;
    if (rev) {
      if (String(rev.tier || "").trim() === "3") continue;
      if (String(rev.threatCategory || "").trim().toLowerCase() === "no historic impact") continue;
    }
    const coordSource = String(it.atlas?.coordSource || "");
    if (
      coordSource === "super_neighborhood" &&
      bLayer &&
      bLayer !== "super_neighborhoods" &&
      bLayer !== "council_districts"
    ) {
      continue;
    }
    const gCoord = raw?.geometry?.type === "Point" ? raw.geometry.coordinates : null;
    const coord = parseValidHarrisCoord(
      it.atlas?.lng ?? it.lng ?? gCoord?.[0],
      it.atlas?.lat ?? it.lat ?? gCoord?.[1]
    );
    if (!coord || !geom || !pointInGeometry(coord[0], coord[1], geom, bbox)) continue;
    const firstHcad = String(it.hcad || rev?.hcad || "")
      .split(/[,;\s|]+/)
      .map((t) => normalizeHcad13(t))
      .find(Boolean) || "";
    const tierLbl = rev?.tier ? `Tier ${rev.tier} Plat` : it.special ? `Plat (${it.special})` : "Plat / Replat Filing";
    upsertThreat({
      source: "plats",
      badge: tierLbl,
      hcadNum: firstHcad,
      buildingId: it.atlas?.buildingId || "",
      title: it.name || rev?.address || it.atlas?.primaryAddress || "Plat Application",
      address: rev?.address || it.atlas?.primaryAddress || "",
      yearBuilt: rev?.yearBuilt || it.atlas?.earliestYearBuilt || 0,
      summary: `${it.section || it.type || "Plat submittal"}${appKey ? ` (#${appKey})` : ""}${
        rev?.why ? ` - ${String(rev.why).slice(0, 140)}` : ""
      }`,
      date: it.hearingDateIso || it.pcDateIso || "",
      url: it.pdf || it.noticePdf || "",
      lng: coord[0],
      lat: coord[1],
      severityRank: String(rev?.tier) === "1" ? 1 : 2,
    });
  }

  // 3. 15-Point Property Watchlist (deduplicating primaryProperty + watchedProperties)
  const wlRoot = liveDatasets.propertyWatchlist || liveDatasets.watchlistData || null;
  const wlCandidates = [];
  if (wlRoot && typeof wlRoot === "object" && !Array.isArray(wlRoot)) {
    if (wlRoot.primaryProperty && typeof wlRoot.primaryProperty === "object") {
      wlCandidates.push(wlRoot.primaryProperty);
    }
    if (Array.isArray(wlRoot.watchedProperties)) {
      wlCandidates.push(...wlRoot.watchedProperties);
    }
  } else if (Array.isArray(wlRoot)) {
    wlCandidates.push(...wlRoot);
  }
  const seenWlIds = new Set();
  for (const w of wlCandidates) {
    if (!w || typeof w !== "object") continue;
    const wid = String(w.id || "").trim();
    const whcad = normalizeHcad13(w.hcadAccount || w.hcad_num || w.hcad);
    const dedupWl = wid || (whcad ? `hcad:${whcad}` : `nm:${w.propertyName || w.address || ""}`);
    if (seenWlIds.has(dedupWl)) continue;
    seenWlIds.add(dedupWl);

    if (w.removedFromWatchlist === true || w.deleted === true || w.isDeleted === true) continue;
    const statusTxt = String(w.trackerStatus ?? w.status ?? "").trim();
    if (statusTxt && TERMINAL_WATCHLIST_STATUS_RE.test(statusTxt)) continue;
    if (String(w.tier || "").trim() === "3") continue;
    if (wid.startsWith("plat-") && w.tierConfirmed === false) continue;

    const coord = parseValidHarrisCoord(w.lng ?? w.lon, w.lat);
    let inside = false;
    if (coord && geom) {
      inside = pointInGeometry(coord[0], coord[1], geom, bbox);
    } else if (
      !coord &&
      bNameNorm &&
      (bLayer === "neighborhoods" || bLayer === "super_neighborhoods" || bLayer === "historic_districts")
    ) {
      const wn = normalizeNameToken(w.neighborhood || "");
      if (wn && wn === bNameNorm) inside = true;
    }
    if (!inside) continue;

    upsertThreat({
      source: "watchlist",
      badge: statusTxt ? `Watchlist: ${statusTxt}` : "PH Watchlist",
      hcadNum: whcad,
      buildingId: w.buildingId || "",
      title: w.propertyName || w.platProjectName || w.address || "Watched Property",
      address: w.address || "",
      yearBuilt: w.yearBuilt || 0,
      summary: w.threatCategory || w.notes || "Tracked on Preservation Houston Property Watchlist",
      date: w.hearingDateIso || w.updatedAt || "",
      lng: coord ? coord[0] : null,
      lat: coord ? coord[1] : null,
      severityRank: /high priority|active|for sale/i.test(statusTxt) ? 1 : 2,
    });
  }

  // 4. HAHC COA & Designation Tracker (active agenda items + demolition/relocation COAs only)
  const hahcRoot = liveDatasets.hahcCoaTracker || liveDatasets.hahcTrackerData || null;
  const hahcList = Array.isArray(hahcRoot)
    ? hahcRoot
    : hahcRoot?.filings || hahcRoot?.features || hahcRoot?.items || [];
  for (const raw of hahcList) {
    const f = raw?.type === "Feature" ? raw.properties || {} : raw || {};
    const isActive = f.is_active_agenda === true;
    const isDemoReloc = String(f.app_category || "") === "demolition_relocation";
    if (!isActive && !isDemoReloc) continue;
    const gCoord = raw?.geometry?.type === "Point" ? raw.geometry.coordinates : null;
    const coord = parseValidHarrisCoord(f.lng ?? f.lon ?? gCoord?.[0], f.lat ?? gCoord?.[1]);
    if (!coord || !geom || !pointInGeometry(coord[0], coord[1], geom, bbox)) continue;

    const outcomeStr = String(f.outcome || f.decision || "").toLowerCase();
    let badge = "HAHC Pending Docket";
    if (!isActive && isDemoReloc) {
      if (outcomeStr.includes("denied")) badge = "HAHC Demolition Denied / COR";
      else if (outcomeStr.includes("approv")) badge = "HAHC Demolition / Relocation COA";
      else badge = "HAHC Demolition Filing";
    } else if (isActive && isDemoReloc) {
      badge = "HAHC Active Demolition Docket";
    }

    upsertThreat({
      source: "hahc",
      badge,
      hcadNum: f.hcad_num || f.HCAD_NUM,
      buildingId: f.building_id || "",
      title: f.landmark_name || f.address || "HAHC COA Filing",
      address: f.address || "",
      yearBuilt: f.year_built || 0,
      summary: `${f.app_type || "COA Filing"}${f.outcome_detail ? ` (${f.outcome_detail})` : ""}`,
      date: f.meeting_date || "",
      url: f.staff_report_url || f.agenda_or_minutes_url || "",
      lng: coord[0],
      lat: coord[1],
      severityRank: isActive && isDemoReloc ? 0 : isActive ? 2 : 3,
    });
  }

  const items = Array.from(mergedByKey.values()).sort((a, b) => {
    if (a.severityRank !== b.severityRank) return a.severityRank - b.severityRank;
    if (b.sources.length !== a.sources.length) return b.sources.length - a.sources.length;
    return String(b.latestDate).localeCompare(String(a.latestDate)) || a.title.localeCompare(b.title);
  });

  return {
    totalUniqueThreats: items.length,
    sourceCounts,
    items,
  };
}

// ============================================================================
// 5. HCAD Parcel Normalizer & Fault-Tolerant Blockface Fetcher (DF-6)
// ============================================================================

const CORP_OWNER_RE =
  /\b(L\s*L\s*C|LLC|PLLC|L\s*P|LP|LLP|LTD|INC|INCORPORATED|CORP|CORPORATION|HOLDINGS|INVESTMENTS|INVESTORS|VENTURES|CAPITAL|PARTNERS|PARTNERSHIP|HOMES|BUILDERS|BUILDING\s+CO|DEVELOPMENT|DEVELOPERS|REALTY|REAL\s+ESTATE|PROPERTIES|ENTERPRISES|EQUITY|ACQUISITIONS|REIT|MORTGAGE|BANK|NATIONAL\s+ASSN)\b/i;
const PUBLIC_EXEMPT_OWNER_RE =
  /\b(CITY\s+OF\s+HOUSTON|HARRIS\s+COUNTY|STATE\s+OF\s+TEXAS|TXDOT|METROPOLITAN\s+TRANSIT|HOUSTON\s+ISD|INDEPENDENT\s+SCHOOL|HOUSING\s+AUTHORITY|FLOOD\s+CONTROL|UNITED\s+STATES|PORT\s+OF\s+HOUSTON|DEPT\s+OF\s+TRANSP)\b/i;
const FAMILY_TRUST_OWNER_RE =
  /\b(TRUST|TRUSTEE|TRUSTEES|LIVING\s+TR|REV\s+TR|REVOCABLE\s+TR|FAM\s+TR|FAMILY\s+TR|IRREVOCABLE\s+TR|TESTAMENTARY\s+TR|ESTATE\s+OF|EST\s+OF|HEIRS\s+OF|ESTATE|TRS)\b/i;

export function classifyBlockfaceOwnerType(ownerName, stateClass = "") {
  const owner = String(ownerName || "").trim();
  const sc = String(stateClass || "").trim().toUpperCase();
  if (PUBLIC_EXEMPT_OWNER_RE.test(owner) || sc === "XJ") {
    return "public_exempt";
  }
  if (CORP_OWNER_RE.test(owner)) return "corporate";
  if (FAMILY_TRUST_OWNER_RE.test(owner)) return "family_trust";
  if (!owner) return "unknown";
  return "individual";
}

function normalizeGeoJsonGeometry(rawGeom) {
  if (!rawGeom) return null;
  if (rawGeom.type === "Polygon" || rawGeom.type === "MultiPolygon") {
    return Array.isArray(rawGeom.coordinates) && rawGeom.coordinates.length > 0 ? rawGeom : null;
  }
  if (Array.isArray(rawGeom.rings) && rawGeom.rings.length > 0) {
    return { type: "Polygon", coordinates: rawGeom.rings };
  }
  return null;
}

export function computeParcelRingSignature(geometry) {
  const rings = extractOuterRings(geometry);
  if (rings.length === 0) return "";
  const pts = [];
  for (const r of rings) {
    for (let i = 0; i < r.length; i += 1) {
      const p = r[i];
      if (!Array.isArray(p) || p.length < 2) continue;
      if (i === r.length - 1 && r[0] && Math.abs(p[0] - r[0][0]) < 1e-7 && Math.abs(p[1] - r[0][1]) < 1e-7) {
        continue;
      }
      pts.push(`${Number(p[0]).toFixed(5)},${Number(p[1]).toFixed(5)}`);
    }
  }
  pts.sort();
  return pts.join("|");
}

/**
 * Normalizes a raw HCAD MapServer/0 parcel feature alongside Atlas footprints and curated overrides.
 * Ensures tax-exempt X* parcels with standing buildings and impr_val=0 remain constructed (DF-6).
 */
export function normalizeBlockfaceParcel(
  rawFeature,
  {
    buildingSummary = null,
    buildingSummariesByHcad = null,
    buildingFootprints = null,
    footprintsByHcad = null,
    curatedOverrides = null,
  } = {}
) {
  if (!rawFeature || typeof rawFeature !== "object") return null;
  const geom = normalizeGeoJsonGeometry(rawFeature.geometry);
  if (!geom) return null;

  const rp = rawFeature.properties || rawFeature.attributes || {};
  const hcadNum = normalizeHcad13(rp.HCAD_NUM ?? rp.hcad_num ?? rp.acct ?? "");
  if (!hcadNum) return null;

  const resolvedFootprints = Array.isArray(buildingFootprints)
    ? buildingFootprints
    : footprintsByHcad instanceof Map
    ? footprintsByHcad.get(hcadNum) || []
    : footprintsByHcad && typeof footprintsByHcad === "object"
    ? footprintsByHcad[hcadNum] || []
    : Array.isArray(rawFeature.buildingFootprints)
    ? rawFeature.buildingFootprints
    : [];
  const resolvedSummary =
    buildingSummary ||
    (buildingSummariesByHcad instanceof Map
      ? buildingSummariesByHcad.get(hcadNum) || null
      : buildingSummariesByHcad && typeof buildingSummariesByHcad === "object"
      ? buildingSummariesByHcad[hcadNum] || null
      : null);

  const ovMap =
    curatedOverrides && typeof curatedOverrides === "object"
      ? curatedOverrides.overrides || curatedOverrides
      : null;
  const parcelOv = ovMap && ovMap[hcadNum] && !ovMap[hcadNum].is_building_override ? ovMap[hcadNum] : null;
  const isSuppressedVacant = Boolean(parcelOv?.suppress_only || rp.suppress_only || rp.is_demolished_vacant);

  const serverAreaRaw =
    rp["Shape.STArea()"] ??
    rp["SHAPE.STArea()"] ??
    rp.Shape_Area ??
    rp.shape_area ??
    rp.land_sqft ??
    null;
  const hasServerArea =
    serverAreaRaw !== null &&
    serverAreaRaw !== undefined &&
    serverAreaRaw !== "" &&
    Number.isFinite(Number(serverAreaRaw)) &&
    Number(serverAreaRaw) > 0;
  const lotAreaSqft = computeHoustonParcelAreaSqFt(geom, hasServerArea ? serverAreaRaw : null);

  // Filter non-suppressed standing building footprints attached to this parcel
  const activeFootprints = [];
  let minFootprintYear = 0;
  let hdFromFootprints = "";
  for (const fp of resolvedFootprints) {
    if (!fp) continue;
    const fpProps = fp.properties || {};
    if (fpProps.suppress_only) continue;
    if (
      ovMap &&
      isEntitySuppressedByOverrides(ovMap, {
        buildingId: fpProps.building_id,
        featureId: fpProps.id,
        hcadNum,
      })
    ) {
      continue;
    }
    const fpGeom = normalizeGeoJsonGeometry(fp.geometry || fp);
    if (!fpGeom) continue;
    activeFootprints.push({
      geometry: fpGeom,
      properties: fpProps,
      areaSqft: computePolygonAreaSqftPlanar(fpGeom),
    });
    const fyr = Number(fpProps.year_built) || 0;
    if (fyr >= 1836 && (minFootprintYear === 0 || fyr < minFootprintYear)) {
      minFootprintYear = fyr;
    }
    if (!hdFromFootprints && fpProps.historic_district) {
      hdFromFootprints = String(fpProps.historic_district).trim();
    }
  }

  const ovYear = !isSuppressedVacant ? Number(parcelOv?.year_built) || 0 : 0;
  const sumYear = !isSuppressedVacant ? Number(resolvedSummary?.year_built) || 0 : 0;
  const rawYear = !isSuppressedVacant ? Number(rp.year_built || rp.yr_impr) || 0 : 0;
  let yearBuilt = 0;
  if (!isSuppressedVacant) {
    if (ovYear >= 1836) yearBuilt = ovYear;
    else if (minFootprintYear >= 1836) yearBuilt = minFootprintYear;
    else if (sumYear >= 1836) yearBuilt = sumYear;
    else if (rawYear >= 1836) yearBuilt = rawYear;
  }

  const stateClass = String(rp.state_class || "").trim().toUpperCase();
  const imprValRaw = rp.impr_val;
  const imprVal =
    imprValRaw === null || imprValRaw === undefined || imprValRaw === ""
      ? null
      : Number.isFinite(Number(imprValRaw))
      ? Number(imprValRaw)
      : null;
  const landVal = Number.isFinite(Number(rp.land_val)) ? Number(rp.land_val) : null;
  const apprVal = Number.isFinite(Number(rp.appr_val)) ? Number(rp.appr_val) : null;
  const mktVal = Number.isFinite(Number(rp.mkt_val)) ? Number(rp.mkt_val) : null;

  const buildingCount = isSuppressedVacant
    ? 0
    : Math.max(activeFootprints.length, Number(resolvedSummary?.building_count) || 0);

  const isTaxExempt = stateClass.startsWith("X");
  const hasStandingStructure =
    !isSuppressedVacant &&
    (activeFootprints.length > 0 ||
      buildingCount > 0 ||
      yearBuilt >= 1836 ||
      (!isTaxExempt && imprVal !== null && imprVal > 0));
  const isVacant = isSuppressedVacant || (!hasStandingStructure && (/^C/.test(stateClass) || (!isTaxExempt && (imprVal === 0 || imprVal === null) && yearBuilt === 0)));

  const address = String(parcelOv?.address || rp.address || "").replace(/\s+/g, " ").trim();
  const owner = String(parcelOv?.owner || rp.owner || "").replace(/\s+/g, " ").trim();
  const subdivision = String(rp.subdivision || "").trim();
  const legalLines = String(rp.legal_lines || "").trim();
  const historicDistrictRaw = String(
    parcelOv?.historic_district || resolvedSummary?.historic_district || hdFromFootprints || rp.historic_district || ""
  ).trim();
  const historicDistrict = /^(outside historic district|outside city district|none|null)$/i.test(
    historicDistrictRaw
  )
    ? ""
    : historicDistrictRaw;

  const repPt = computeGeometryRepresentativePoint(geom);

  return {
    hcadNum,
    address,
    parsedAddress: parseStreetAddressParts(address),
    owner,
    ownerType: classifyBlockfaceOwnerType(owner, stateClass),
    subdivision,
    legalLines,
    stateClass,
    isTaxExempt,
    imprVal,
    landVal,
    apprVal,
    mktVal,
    lotAreaSqft,
    areaSource: hasServerArea ? "server_starea" : "planar_survey_ft",
    yearBuilt,
    buildingCount,
    hasStandingStructure,
    isConstructed: hasStandingStructure,
    isVacant,
    isSuppressedVacant,
    historicDistrict,
    geometry: geom,
    bbox: computeGeometryBbox(geom),
    centroid: repPt,
    ringSignature: computeParcelRingSignature(geom),
    footprints: activeFootprints,
  };
}

async function queryHcadArcgisWithFallback(
  baseParamsObj,
  {
    fetchImpl = typeof fetch === "function" ? fetch.bind(globalThis) : null,
    endpointUrl = HCAD_PUBLIC_QUERY_URL,
    signal = undefined,
    outFieldsCandidates = [
      HCAD_BLOCKFACE_OUT_FIELDS,
      HCAD_BLOCKFACE_FALLBACK_OUT_FIELDS,
      HCAD_BLOCKFACE_CORE_OUT_FIELDS,
    ],
  } = {}
) {
  if (typeof fetchImpl !== "function") {
    throw new Error("Fetch implementation unavailable for HCAD blockface query.");
  }
  const cleanEndpoint = String(endpointUrl || HCAD_PUBLIC_QUERY_URL).replace(/\/+$/, "");
  const queryUrl = cleanEndpoint.endsWith("/query") ? cleanEndpoint : `${cleanEndpoint}/query`;
  let lastError = null;

  for (const outFields of outFieldsCandidates) {
    const params = new URLSearchParams({
      ...baseParamsObj,
      outFields,
      returnGeometry: "true",
      outSR: "4326",
      f: "geojson",
    });
    const resp = await fetchImpl(`${queryUrl}?${params.toString()}`, { signal });
    if (!resp || !resp.ok) {
      lastError = new Error(`HCAD MapServer HTTP ${resp ? resp.status : "error"}`);
      continue;
    }
    const payload = await resp.json();
    if (payload && payload.error) {
      lastError = new Error(
        `HCAD ArcGIS error ${payload.error.code || ""}: ${payload.error.message || "Invalid query"}`
      );
      continue;
    }
    return payload;
  }
  throw lastError || new Error("HCAD parcel query failed across all outFields tiers.");
}

/**
 * Resolves a single 13-digit HCAD parcel directly from public_query/MapServer/0 (for cold-boot #ch42=).
 */
export async function fetchHcadParcelByAccount(
  rawHcadNum,
  {
    fetchImpl = typeof fetch === "function" ? fetch.bind(globalThis) : null,
    endpointUrl = HCAD_PUBLIC_QUERY_URL,
    signal = undefined,
  } = {}
) {
  const hcad = normalizeHcad13(rawHcadNum);
  if (!hcad) return null;
  const payload = await queryHcadArcgisWithFallback(
    { where: `HCAD_NUM='${hcad}'` },
    { fetchImpl, endpointUrl, signal }
  );
  const feats = Array.isArray(payload?.features) ? payload.features : [];
  return feats[0] || null;
}

/**
 * Fetches all HCAD tax parcels within a blockface envelope around a seed point/parcel,
 * splitting recursively if ArcGIS indicates exceededTransferLimit (DF-6).
 */
export async function fetchBlockfaceParcelsAroundSeed(
  seedInput,
  {
    radiusMeters = 260,
    fetchImpl = typeof fetch === "function" ? fetch.bind(globalThis) : null,
    endpointUrl = HCAD_PUBLIC_QUERY_URL,
    signal = undefined,
    maxRecordCount = 2000,
    maxSplitDepth = 2,
  } = {}
) {
  let lng = Number(seedInput?.lng ?? seedInput?.lon ?? seedInput?.centroid?.[0]);
  let lat = Number(seedInput?.lat ?? seedInput?.centroid?.[1]);
  if ((!Number.isFinite(lng) || !Number.isFinite(lat)) && seedInput?.geometry) {
    const pt = computeGeometryRepresentativePoint(normalizeGeoJsonGeometry(seedInput.geometry));
    if (pt) {
      lng = pt[0];
      lat = pt[1];
    }
  }
  if (!Number.isFinite(lng) || !Number.isFinite(lat)) {
    throw new Error("Seed parcel coordinates are required to query neighboring blockface lots.");
  }

  const { mPerDegLng, mPerDegLat } = getHoustonSurveyFeetScale(lat);
  const dLng = Math.max(40, Number(radiusMeters) || 260) / mPerDegLng;
  const dLat = Math.max(40, Number(radiusMeters) || 260) / mPerDegLat;
  const rootBox = {
    west: Number((lng - dLng).toFixed(6)),
    south: Number((lat - dLat).toFixed(6)),
    east: Number((lng + dLng).toFixed(6)),
    north: Number((lat + dLat).toFixed(6)),
  };

  const fetchBox = async (bbox, depth) => {
    const payload = await queryHcadArcgisWithFallback(
      {
        geometry: `${bbox.west},${bbox.south},${bbox.east},${bbox.north}`,
        geometryType: "esriGeometryEnvelope",
        inSR: "4326",
        spatialRel: "esriSpatialRelIntersects",
      },
      { fetchImpl, endpointUrl, signal }
    );
    const feats = Array.isArray(payload?.features) ? payload.features : [];
    const truncated =
      Boolean(payload?.properties?.exceededTransferLimit) ||
      Boolean(payload?.exceededTransferLimit) ||
      feats.length >= maxRecordCount;
    if (truncated && depth < maxSplitDepth) {
      const midLng = Number(((bbox.west + bbox.east) / 2).toFixed(6));
      const midLat = Number(((bbox.south + bbox.north) / 2).toFixed(6));
      const quads = [
        { west: bbox.west, south: midLat, east: midLng, north: bbox.north },
        { west: midLng, south: midLat, east: bbox.east, north: bbox.north },
        { west: bbox.west, south: bbox.south, east: midLng, north: midLat },
        { west: midLng, south: bbox.south, east: bbox.east, north: midLat },
      ];
      const parts = await Promise.all(quads.map((q) => fetchBox(q, depth + 1)));
      return parts.flat();
    }
    return feats;
  };

  const allRaw = await fetchBox(rootBox, 0);
  const deduped = [];
  const seen = new Set();
  for (const f of allRaw) {
    const hid = normalizeHcad13(f?.properties?.HCAD_NUM ?? f?.properties?.hcad_num);
    const key = hid || JSON.stringify(f?.geometry?.coordinates?.[0]?.[0] || []);
    if (seen.has(key)) continue;
    seen.add(key);
    deduped.push(f);
  }
  return deduped;
}

// ============================================================================
// 6. Houston Address Normalization & Blockface Assembly Engine (DF-3, DF-5)
// ============================================================================

const STREET_DIR_MAP = {
  NORTH: "N",
  SOUTH: "S",
  EAST: "E",
  WEST: "W",
  N: "N",
  S: "S",
  E: "E",
  W: "W",
};

const STREET_SUFFIX_MAP = {
  STREET: "ST",
  ST: "ST",
  AVENUE: "AVE",
  AVE: "AVE",
  AV: "AVE",
  BOULEVARD: "BLVD",
  BLVD: "BLVD",
  DRIVE: "DR",
  DR: "DR",
  ROAD: "RD",
  RD: "RD",
  LANE: "LN",
  LN: "LN",
  COURT: "CT",
  CT: "CT",
  CIRCLE: "CIR",
  CIR: "CIR",
  PLACE: "PL",
  PL: "PL",
  PARKWAY: "PKWY",
  PKWY: "PKWY",
  PKY: "PKWY",
  SPEEDWAY: "SPWY",
  SPWY: "SPWY",
  TERRACE: "TER",
  TER: "TER",
  TRAIL: "TRL",
  TRL: "TRL",
  WAY: "WAY",
  SQUARE: "SQ",
  SQ: "SQ",
  LOOP: "LOOP",
  FWY: "FWY",
  FREEWAY: "FWY",
  HWY: "HWY",
  HIGHWAY: "HWY",
};

/**
 * Parses Houston/HCAD situs addresses including double-spaced directionals ('2915 W  BELLFORT AVE'),
 * '1/2' half-street or upstairs designations ('202 E 31ST 1/2 ST', '714 1/2 PRAIRIE AVE'),
 * trailing unit designators ('711 HEIGHTS BLVD # A', '711 HEIGHTS BLVD A'), and '0 <STREET>' reserves.
 */
export function parseStreetAddressParts(rawAddr) {
  let s = String(rawAddr || "")
    .toUpperCase()
    .replace(/[.,'"]/g, " ")
    .replace(/\bHOUSTON(?:\s+HARRIS)?(?:\s+TX)?(?:\s+\d{5}(?:-\d{4})?)?\s*$/i, " ")
    .replace(/\s+\d{5}(?:-\d{4})?\s*$/, " ")
    .replace(/\s+/g, " ")
    .trim();

  if (!s) {
    return {
      raw: "",
      houseNumber: null,
      houseSuffix: "",
      parity: "unknown",
      hundredBlock: null,
      streetKey: "",
      streetDisplay: "",
      unitSuffix: "",
      isZeroAddress: false,
    };
  }

  let unitSuffix = "";
  const hashIdx = s.indexOf("#");
  if (hashIdx >= 0) {
    unitSuffix = s.slice(hashIdx).trim();
    s = s.slice(0, hashIdx).trim();
  }
  const unitTailM = s.match(/\s+(?:APT|UNIT|STE|SUITE|BLDG|BLD|FL|FLOOR|RM|ROOM)\s+([A-Z0-9-]+.*)$/);
  if (unitTailM) {
    unitSuffix = unitSuffix || unitTailM[0].trim();
    s = s.slice(0, unitTailM.index).trim();
  }

  const m = s.match(/^(\d+)([A-Z]?)(?:\s+(1\/2))?\s+(.+)$/);
  if (!m) {
    return {
      raw: s,
      houseNumber: null,
      houseSuffix: "",
      parity: "unknown",
      hundredBlock: null,
      streetKey: s,
      streetDisplay: s,
      unitSuffix,
      isZeroAddress: false,
    };
  }

  const houseNumber = Number(m[1]);
  let houseSuffix = m[2] || "";
  const maybeHalfAfterNum = m[3] || "";
  let rest = m[4].trim();

  // Distinguish upstairs/rear '714 1/2 PRAIRIE AVE' from half-streets like '202 E 31ST 1/2 ST':
  // In '202 E 31ST 1/2 ST', '1/2' appears inside `rest` right after '31ST', whereas in
  // '714 1/2 PRAIRIE AVE', m[3] captured '1/2' immediately after the house number!
  if (maybeHalfAfterNum) {
    houseSuffix = houseSuffix ? `${houseSuffix} 1/2` : "1/2";
  }

  const tokens = rest.split(/\s+/).filter(Boolean);
  if (tokens.length >= 2 && STREET_DIR_MAP[tokens[0]]) {
    tokens[0] = STREET_DIR_MAP[tokens[0]];
  }

  // Strip trailing standalone letter/number unit token AFTER a recognized street type suffix
  // e.g. '711 HEIGHTS BLVD A' -> street ends at 'BLVD', unit = 'A'
  if (tokens.length >= 3) {
    const penultimate = STREET_SUFFIX_MAP[tokens[tokens.length - 2]];
    const lastTok = tokens[tokens.length - 1];
    if (penultimate && /^(?:[A-Z]|\d{1,3}|REAR|FRONT)$/.test(lastTok) && !STREET_DIR_MAP[lastTok]) {
      unitSuffix = unitSuffix || lastTok;
      tokens.pop();
    }
  }
  if (tokens.length >= 2) {
    const lastIdx = tokens.length - 1;
    if (STREET_SUFFIX_MAP[tokens[lastIdx]]) {
      tokens[lastIdx] = STREET_SUFFIX_MAP[tokens[lastIdx]];
    }
  }

  const streetKey = tokens.join(" ");
  const isZero = houseNumber === 0;
  const parity = isZero || !Number.isFinite(houseNumber) ? "unknown" : houseNumber % 2 === 0 ? "even" : "odd";
  const hundredBlock = !isZero && Number.isFinite(houseNumber) ? Math.floor(houseNumber / 100) * 100 : null;

  return {
    raw: String(rawAddr || "").trim(),
    houseNumber,
    houseSuffix,
    parity,
    hundredBlock,
    streetKey,
    streetDisplay: streetKey,
    unitSuffix,
    isZeroAddress: isZero,
  };
}

const EXCLUDED_CIVIC_RELIGIOUS_RE =
  /\b(PARK\b|PARKS\s+DEPT|COMMUNITY\s+CENTER|RECREATION\s+CENTER|PUBLIC\s+LIBRARY|\bLIBRARY\b|CHURCH|CHAPEL|CATHEDRAL|PARISH|DIOCESE|ARCHDIOCESE|BAPTIST|METHODIST|CATHOLIC|LUTHERAN|PRESBYTERIAN|EPISCOPAL|SYNAGOGUE|TEMPLE|MOSQUE|ISLAMIC|MINISTR(?:Y|IES)|CONGREGATION|\bISD\b|SCHOOL\s+DIST|ELEMENTARY|MIDDLE\s+SCHOOL|HIGH\s+SCHOOL|ACADEMY\s+SCHOOL)\b/i;

const UTILITY_DETENTION_RE =
  /\b(UTILITY|EASEMENT|DRAINAGE|DETENTION|RETENTION|FLOOD\s+CONTROL|HCFCD|LIFT\s+STATION|SUBSTATION|WATER\s+PLANT|PIPELINE|CENTERPOINT|HL&P|RIGHT\s+OF\s+WAY|\bROW\b)\b/i;

const COMMON_RESERVE_RE =
  /\b(COMMON\s+AREA|SHARED\s+DRIVE(?:WAY)?|PRIVATE\s+DRIVE|PRIVATE\s+STREET|PRIVATE\s+ALLEY|ACCESS\s+ESM|HOMEOWNERS\s+ASS|HOA\b|PROP(?:ERTY)?\s+OWNERS\s+ASS|POA\b|CONDO\s+ASSN|GUEST\s+PARKING|OPEN\s+SPACE\s+RES)\b/i;

/**
 * Classifies a normalized parcel across all 4 COH Chapter 42 statutory denominators (DF-2):
 *   1. Sec. 42-197(b)(4) / Sec. 42-170(b)(4) SFR Eligibility (>= 60% Block / >= 80% Area)
 *   2. Sec. 42-202 Special Minimum Lot Size (SMLS) non-governmental/non-utility universe
 *   3. Sec. 42-173 Special Minimum Building Line (SMBL) constructed-buildings-only universe
 *   4. Sec. 42-200(e) / Sec. 42-172(f) >= 51% qualifying blockface land-area fast-track universe
 */
export function classifyParcelForChapter42(parcel, overrides = {}) {
  const sc = String(parcel?.stateClass || "").trim().toUpperCase();
  const owner = String(parcel?.owner || "");
  const legal = String(parcel?.legalLines || "");
  const combinedText = `${owner} | ${legal} | ${parcel?.subdivision || ""}`;
  const areaSqft = Number(parcel?.lotAreaSqft) || 0;
  const warnings = [];

  const manualUse = String(overrides?.landUseOverride || "").trim().toLowerCase();
  const vacantWasSfr5Yr = Boolean(overrides?.vacantWasSfrPast5Years ?? parcel?.vacantWasSfrPast5Years);

  const isZeroOrCommonReserve =
    manualUse === "common_reserve" ||
    areaSqft <= 1 ||
    COMMON_RESERVE_RE.test(combinedText) ||
    (parcel?.parsedAddress?.isZeroAddress &&
      (/\bRES(?:ERVE)?\b/i.test(legal) || areaSqft < 1200) &&
      !parcel?.hasStandingStructure);

  if (isZeroOrCommonReserve && !manualUse) {
    warnings.push("Segregated common/shared-driveway reserve or zero-area account.");
  }
  if (isZeroOrCommonReserve && manualUse !== "sfr" && manualUse !== "vacant_residential") {
    return {
      landUseCategory: "common_reserve",
      landUseLabel: "Common Area / Shared Driveway Reserve",
      inSfrDenominator: false,
      isSfrNumerator: false,
      inSmlsUniverse: false,
      inSmblUniverse: false,
      inPetitionAreaUniverse: false,
      warnings,
    };
  }

  const isUtilityOrGov =
    manualUse === "utility_gov" ||
    (!manualUse &&
      (UTILITY_DETENTION_RE.test(combinedText) ||
        PUBLIC_EXEMPT_OWNER_RE.test(owner) ||
        /^[J]/.test(sc)));

  const isExcludedCivicReligious =
    manualUse === "excluded_civic_religious" ||
    (!manualUse && (EXCLUDED_CIVIC_RELIGIOUS_RE.test(combinedText) || (sc.startsWith("X") && !isUtilityOrGov)));

  if (isUtilityOrGov) {
    return {
      landUseCategory: "utility_governmental",
      landUseLabel: "Governmental / Utility / Detention (Excluded)",
      inSfrDenominator: false,
      isSfrNumerator: false,
      inSmlsUniverse: false,
      inSmblUniverse: false,
      inPetitionAreaUniverse: false,
      warnings,
    };
  }

  if (isExcludedCivicReligious) {
    // Under Sec. 42-197(b)(4), church/school/park/library/community-center lots are excluded
    // from the 60% SFR denominator; private non-governmental institutional lots remain in Sec. 42-202
    // unless government/utility, so expose clear warnings and respect tax-exempt standing footprints (DF-6).
    if (parcel?.hasStandingStructure && parcel?.imprVal === 0) {
      warnings.push("Tax-exempt institutional parcel with standing structure (impr_val = $0; not vacant).");
    }
    return {
      landUseCategory: "excluded_civic_religious",
      landUseLabel: "Park / School / Library / Religious (Excl. from 60% SFR Test)",
      inSfrDenominator: false,
      isSfrNumerator: false,
      inSmlsUniverse: !PUBLIC_EXEMPT_OWNER_RE.test(owner),
      inSmblUniverse: Boolean(parcel?.hasStandingStructure) && !PUBLIC_EXEMPT_OWNER_RE.test(owner),
      inPetitionAreaUniverse: !PUBLIC_EXEMPT_OWNER_RE.test(owner),
      warnings,
    };
  }

  if (parcel?.isVacant || manualUse === "vacant_residential") {
    const isResVacant =
      manualUse === "vacant_residential" ||
      sc === "C1" ||
      sc.startsWith("A") ||
      sc === "";
    if (parcel?.isSuppressedVacant) {
      warnings.push("Recent demolition (suppress_only vacant lot); toggle if SFR existed within past 5 years.");
    } else {
      warnings.push("Vacant lot: included in SMLS area calculation, excluded from SMBL setback ranking.");
    }
    return {
      landUseCategory: isResVacant ? "vacant_residential" : "vacant_other",
      landUseLabel: vacantWasSfr5Yr
        ? "Vacant Lot (SFR Within Past 5 Yrs - Sec. 42-197(b)(4))"
        : isResVacant
        ? "Vacant Residential Lot"
        : "Vacant Commercial / Other Lot",
      inSfrDenominator: true,
      isSfrNumerator: Boolean(vacantWasSfr5Yr),
      inSmlsUniverse: true,
      inSmblUniverse: false,
      inPetitionAreaUniverse: true,
      warnings,
    };
  }

  const condoUnits = Number(parcel?.condoUnitCount) || 1;
  const isStackedMultiUnitCondo = condoUnits > 2;
  const isSingleFamily =
    manualUse === "sfr" ||
    (!manualUse &&
      !isStackedMultiUnitCondo &&
      (sc === "A1" || sc === "A2" || sc === "A3" || sc === "A4" || (sc === "" && parcel?.hasStandingStructure)));

  if (isSingleFamily) {
    return {
      landUseCategory: "sfr",
      landUseLabel: sc === "A2" || sc === "A4" ? "Townhome / Condo Residential" : "Single-Family Residential",
      inSfrDenominator: true,
      isSfrNumerator: true,
      inSmlsUniverse: true,
      inSmblUniverse: Boolean(parcel?.hasStandingStructure),
      inPetitionAreaUniverse: true,
      warnings,
    };
  }

  if (isStackedMultiUnitCondo && !manualUse) {
    warnings.push(
      `Stacked condominium regime (${condoUnits} units on 1 lot) classified as multi-family under COH Code Sec. 42-1 (>2 units on lot).`
    );
  }
  const isMultiFamily =
    manualUse === "multifamily" || sc.startsWith("B") || isStackedMultiUnitCondo;
  return {
    landUseCategory: isMultiFamily ? "multifamily" : "commercial_industrial",
    landUseLabel: isMultiFamily ? "Multi-Family Residential (Non-SFR)" : "Commercial / Non-SFR",
    inSfrDenominator: true,
    isSfrNumerator: false,
    inSmlsUniverse: true,
    inSmblUniverse: Boolean(parcel?.hasStandingStructure),
    inPetitionAreaUniverse: true,
    warnings,
  };
}

/**
 * Estimates perpendicular front building line setback (in US survey feet) from the target street-aligned
 * parcel front edge to the primary standing building footprint (DF-5).
 *
 * Strictly returns { setbackFt: null, setbackSource: 'unavailable' } (NEVER 0 ft) when a parcel has
 * no non-suppressed footprint, and ignores detached sheds < 400 sq ft when a main building exists.
 */
export function estimateParcelFrontSetbackFt(parcel, streetContext = null) {
  const warnings = [];
  if (!parcel) {
    return {
      setbackFt: null,
      setbackSource: "unavailable",
      frontEdgeSegment: null,
      frontEdgeBearingDeg: null,
      warnings: ["No standing primary structure on parcel."],
    };
  }

  const origin = getGeometryReferenceOrigin(parcel.geometry);
  if (!origin) {
    return {
      setbackFt: null,
      setbackSource: "unavailable",
      frontEdgeSegment: null,
      frontEdgeBearingDeg: null,
      warnings,
    };
  }
  const { refLng, refLat } = origin;
  const { ftPerDegLng, ftPerDegLat } = getHoustonSurveyFeetScale(refLat);
  const toFt = (pt) => [(pt[0] - refLng) * ftPerDegLng, (pt[1] - refLat) * ftPerDegLat];

  const outerRings = extractOuterRings(parcel.geometry);
  const segments = [];
  for (const ring of outerRings) {
    for (let i = 0; i < ring.length - 1; i += 1) {
      const a = ring[i];
      const b = ring[i + 1];
      const [ax, ay] = toFt(a);
      const [bx, by] = toFt(b);
      const lenFt = Math.hypot(bx - ax, by - ay);
      if (lenFt < 4.0) continue;
      segments.push({
        aWgs: a,
        bWgs: b,
        ax,
        ay,
        bx,
        by,
        mx: (ax + bx) * 0.5,
        my: (ay + by) * 0.5,
        lenFt,
        bearingDeg: segmentBearingDeg(ax, ay, bx, by),
      });
    }
  }
  if (segments.length === 0) {
    return {
      setbackFt: null,
      setbackSource: "unavailable",
      frontEdgeSegment: null,
      frontEdgeBearingDeg: null,
      warnings,
    };
  }

  const parcelCenterFt = parcel.centroid ? toFt(parcel.centroid) : [0, 0];
  let frontSegments = [];

  if (streetContext && Number.isFinite(streetContext.bearingDeg)) {
    // Keep edges roughly parallel to the target street axis (within 38 deg)
    let aligned = segments.filter(
      (seg) => axialAngleDiffDeg(seg.bearingDeg, streetContext.bearingDeg) <= 38.0
    );
    if (aligned.length === 0) {
      aligned = segments
        .slice()
        .sort(
          (a, b) =>
            axialAngleDiffDeg(a.bearingDeg, streetContext.bearingDeg) -
            axialAngleDiffDeg(b.bearingDeg, streetContext.bearingDeg)
        )
        .slice(0, 2);
    }

    // Between front edge and rear alley edge (both parallel to the street!), pick the side facing
    // the blockface street line / front normal direction!
    if (streetContext.streetPointWgs) {
      const [sx, sy] = toFt(streetContext.streetPointWgs);
      const ux = Math.cos((streetContext.bearingDeg * Math.PI) / 180.0);
      const uy = Math.sin((streetContext.bearingDeg * Math.PI) / 180.0);
      const perpSigned = (px, py) => (px - sx) * -uy + (py - sy) * ux;
      aligned.sort((a, b) => Math.abs(perpSigned(a.mx, a.my)) - Math.abs(perpSigned(b.mx, b.my)));
      const minAbs = Math.abs(perpSigned(aligned[0].mx, aligned[0].my));
      frontSegments = aligned.filter(
        (seg) => Math.abs(Math.abs(perpSigned(seg.mx, seg.my)) - minAbs) <= 18.0
      );
    } else if (streetContext.frontNormal) {
      const [nx, ny] = streetContext.frontNormal;
      aligned.sort(
        (a, b) =>
          (b.mx - parcelCenterFt[0]) * nx +
          (b.my - parcelCenterFt[1]) * ny -
          ((a.mx - parcelCenterFt[0]) * nx + (a.my - parcelCenterFt[1]) * ny)
      );
      const bestProj =
        (aligned[0].mx - parcelCenterFt[0]) * nx + (aligned[0].my - parcelCenterFt[1]) * ny;
      frontSegments = aligned.filter(
        (seg) =>
          bestProj -
            ((seg.mx - parcelCenterFt[0]) * nx + (seg.my - parcelCenterFt[1]) * ny) <=
          18.0
      );
    } else {
      frontSegments = [aligned[0]];
    }
  } else {
    // Fallback without street axis: choose the shorter street-frontage side closest to primary building
    segments.sort((a, b) => a.lenFt - b.lenFt);
    frontSegments = [segments[0]];
    warnings.push("Street axis inferred from single lot; verify front-edge orientation.");
  }

  const frontEdgeSegment = frontSegments[0] ? [frontSegments[0].aWgs, frontSegments[0].bWgs] : null;
  const frontEdgeBearingDeg = frontSegments[0]?.bearingDeg ?? null;

  if (parcel.isVacant || parcel.isSuppressedVacant) {
    return {
      setbackFt: null,
      setbackSource: "unavailable",
      frontEdgeSegment,
      frontEdgeBearingDeg,
      warnings: [...warnings, "No standing primary structure on parcel."],
    };
  }

  const fps = Array.isArray(parcel.footprints) ? parcel.footprints.filter((f) => f?.geometry) : [];
  if (fps.length === 0) {
    warnings.push(
      "Building footprint polygon unavailable at current tile zoom; enter survey/aerial setback manually."
    );
    return {
      setbackFt: null,
      setbackSource: "unavailable",
      frontEdgeSegment,
      frontEdgeBearingDeg,
      warnings,
    };
  }

  // Prefer main structures >= 400 sq ft over tiny accessory sheds/carports/gatehouses.
  // When dense street-tree canopy hides the front house from satellite footprints and ONLY a
  // < 400 sq ft backyard shed is digitized, return null so a rear shed does not skew SMBL!
  const sizable = fps.filter(
    (f) =>
      (Number(f.areaSqft) || computePolygonAreaSqftPlanar(f.geometry)) >=
      CH42_PRIMARY_FOOTPRINT_MIN_SQFT
  );
  if (sizable.length === 0) {
    warnings.push(
      "Only small (< 400 sq ft) accessory footprint found; enter primary house setback manually."
    );
    return {
      setbackFt: null,
      setbackSource: "unavailable",
      frontEdgeSegment,
      frontEdgeBearingDeg,
      warnings,
    };
  }
  const chosenPool = sizable;

  // Among sizable buildings on parcel (e.g., main house in front vs. 450 sq ft rear garage apartment),
  // score by weighting area strongly while favoring the primary house closer to the front street line.
  const fpCandidates = chosenPool.map((fp) => {
    const fpRings = extractOuterRings(fp.geometry);
    let minDist = Infinity;
    for (const ring of fpRings) {
      for (let i = 0; i < ring.length - 1; i += 1) {
        const [px1, py1] = toFt(ring[i]);
        const [px2, py2] = toFt(ring[i + 1]);
        for (const fseg of frontSegments) {
          const d = segmentToSegmentDistFt(px1, py1, px2, py2, fseg.ax, fseg.ay, fseg.bx, fseg.by);
          if (d < minDist) minDist = d;
        }
      }
    }
    const area = Number(fp.areaSqft) || computePolygonAreaSqftPlanar(fp.geometry);
    return { fp, area, minDist };
  }).filter((c) => Number.isFinite(c.minDist));

  if (fpCandidates.length === 0) {
    return {
      setbackFt: null,
      setbackSource: "unavailable",
      frontEdgeSegment,
      frontEdgeBearingDeg,
      warnings,
    };
  }

  const maxArea = Math.max(...fpCandidates.map((c) => c.area));
  // Keep buildings that are at least 45% of the largest building's footprint (filtering out rear detached garages)
  const mainHouseCandidates = fpCandidates.filter((c) => c.area >= maxArea * 0.45);
  mainHouseCandidates.sort((a, b) => a.minDist - b.minDist);
  const winner = mainHouseCandidates[0];
  if (fps.length > 1) {
    warnings.push(`Multiple structures (${fps.length}) on parcel; measured primary front building.`);
  }

  const setbackFt = Number(Math.max(0.1, winner.minDist).toFixed(1));
  return {
    setbackFt,
    setbackSource: "footprint_estimate",
    frontEdgeSegment: frontSegments[0]
      ? [frontSegments[0].aWgs, frontSegments[0].bWgs]
      : null,
    frontEdgeBearingDeg: frontSegments[0]?.bearingDeg ?? null,
    warnings,
  };
}

function collapseStackedCondoParcels(parcels, seedHcadNum) {
  const byRing = new Map();
  const passthrough = [];
  for (const p of parcels) {
    if (!p) continue;
    if (!p.ringSignature || p.lotAreaSqft <= 1) {
      passthrough.push({ ...p, condoUnitCount: 1, collapsedHcads: [p.hcadNum] });
      continue;
    }
    const arr = byRing.get(p.ringSignature);
    if (!arr) {
      byRing.set(p.ringSignature, [p]);
    } else {
      arr.push(p);
    }
  }
  const out = [...passthrough];
  for (const group of byRing.values()) {
    if (group.length === 1) {
      out.push({ ...group[0], condoUnitCount: 1, collapsedHcads: [group[0].hcadNum] });
      continue;
    }
    // Stacked condo accounts sharing identical physical lot boundary (DF-2)
    group.sort((a, b) => {
      if (a.hcadNum === seedHcadNum) return -1;
      if (b.hcadNum === seedHcadNum) return 1;
      return (b.lotAreaSqft || 0) - (a.lotAreaSqft || 0) || a.hcadNum.localeCompare(b.hcadNum);
    });
    const primary = group[0];
    const anyConstructed = group.some((g) => g.hasStandingStructure);
    const minYr = group
      .map((g) => g.yearBuilt)
      .filter((y) => y >= 1836)
      .reduce((acc, y) => (acc === 0 || y < acc ? y : acc), 0);
    const mergedFootprints = [];
    const seenFp = new Set();
    for (const g of group) {
      for (const fp of g.footprints || []) {
        const sig = computeParcelRingSignature(fp.geometry);
        if (!seenFp.has(sig)) {
          seenFp.add(sig);
          mergedFootprints.push(fp);
        }
      }
    }
    out.push({
      ...primary,
      hasStandingStructure: anyConstructed,
      isConstructed: anyConstructed,
      isVacant: !anyConstructed && primary.isVacant,
      yearBuilt: minYr || primary.yearBuilt,
      footprints: mergedFootprints,
      condoUnitCount: group.length,
      collapsedHcads: group.map((g) => g.hcadNum),
    });
  }
  return out;
}

function splitContiguousClusterWithSeed(sameSideParcels, seedHcadNum, axisUx, axisUy, originLng, originLat) {
  if (sameSideParcels.length <= 1) return sameSideParcels;
  const { ftPerDegLng, ftPerDegLat } = getHoustonSurveyFeetScale(originLat);
  const scored = sameSideParcels.map((p) => {
    const cx = ((p.centroid?.[0] ?? originLng) - originLng) * ftPerDegLng;
    const cy = ((p.centroid?.[1] ?? originLat) - originLat) * ftPerDegLat;
    return { p, proj: cx * axisUx + cy * axisUy };
  });
  scored.sort((a, b) => a.proj - b.proj || a.p.hcadNum.localeCompare(b.p.hcadNum));

  // Build adjacency graph using edge-to-edge gap <= CH42_CROSS_STREET_GAP_METERS (16 m)
  // AND guard against jumping across a numbered cross-street 100-block boundary over a street gap.
  const seedIdx = scored.findIndex(
    (s) => s.p.hcadNum === seedHcadNum || (s.p.collapsedHcads || []).includes(seedHcadNum)
  );
  if (seedIdx < 0) return scored.map((s) => s.p);

  const includedIdx = new Set([seedIdx]);
  // Expand left from seed
  for (let i = seedIdx - 1; i >= 0; i -= 1) {
    // Check minimum gap to ANY already-accepted lot within the adjacent 2 positions (handles flag lots)
    let minGap = Infinity;
    for (const accIdx of includedIdx) {
      if (accIdx - i <= 3) {
        const g = minPolygonGapMeters(scored[i].p.geometry, scored[accIdx].p.geometry);
        if (g < minGap) minGap = g;
      }
    }
    if (minGap > CH42_CROSS_STREET_GAP_METERS) break;
    includedIdx.add(i);
  }
  // Expand right from seed
  for (let i = seedIdx + 1; i < scored.length; i += 1) {
    let minGap = Infinity;
    for (const accIdx of includedIdx) {
      if (i - accIdx <= 3) {
        const g = minPolygonGapMeters(scored[i].p.geometry, scored[accIdx].p.geometry);
        if (g < minGap) minGap = g;
      }
    }
    if (minGap > CH42_CROSS_STREET_GAP_METERS) break;
    includedIdx.add(i);
  }

  return scored.filter((_, idx) => includedIdx.has(idx)).map((s) => s.p);
}

/**
 * Dynamically assembles a contiguous single blockface (plus optional opposing blockface,
 * corner-lot review candidates, and cul-de-sac mixed-parity neighbors) from a seed parcel (DF-5).
 */
export function assembleBlockfaceFromSeedParcel(seedParcelInput, candidateParcels = [], options = {}) {
  if (!seedParcelInput) {
    throw new Error("A valid seed parcel is required to assemble a Chapter 42 blockface.");
  }
  const seedNorm = seedParcelInput.parsedAddress
    ? seedParcelInput
    : normalizeBlockfaceParcel(seedParcelInput, options);
  if (!seedNorm) {
    throw new Error("Seed parcel lacks valid HCAD number or polygon geometry.");
  }

  const rawPool = [];
  const seenHcads = new Set([seedNorm.hcadNum]);
  rawPool.push(seedNorm);
  for (const cand of candidateParcels || []) {
    const n = cand?.parsedAddress ? cand : normalizeBlockfaceParcel(cand, options);
    if (!n || seenHcads.has(n.hcadNum)) continue;
    seenHcads.add(n.hcadNum);
    rawPool.push(n);
  }

  const dedupedPool = collapseStackedCondoParcels(rawPool, seedNorm.hcadNum);
  const seed =
    dedupedPool.find(
      (p) => p.hcadNum === seedNorm.hcadNum || (p.collapsedHcads || []).includes(seedNorm.hcadNum)
    ) || seedNorm;

  const targetStreetKey = String(
    options.overrideStreetKey || seed.parsedAddress?.streetKey || ""
  ).trim();
  const seedParity = seed.parsedAddress?.parity || "unknown";
  const seedCentroid = seed.centroid || [HOUSTON_DEFAULT_LNG_DEG, HOUSTON_DEFAULT_LAT_DEG];
  const [originLng, originLat] = seedCentroid;
  const { ftPerDegLng, ftPerDegLat } = getHoustonSurveyFeetScale(originLat);
  const toFt = (pt) => [(pt[0] - originLng) * ftPerDegLng, (pt[1] - originLat) * ftPerDegLat];

  const sameStreetAll = dedupedPool.filter(
    (p) => targetStreetKey && p.parsedAddress?.streetKey === targetStreetKey
  );
  const sameParityCandidates = sameStreetAll.filter(
    (p) =>
      p.hcadNum === seed.hcadNum ||
      p.parsedAddress?.isZeroAddress ||
      seedParity === "unknown" ||
      p.parsedAddress?.parity === seedParity
  );
  const oppositeParityCandidates = sameStreetAll.filter(
    (p) =>
      p.hcadNum !== seed.hcadNum &&
      !p.parsedAddress?.isZeroAddress &&
      seedParity !== "unknown" &&
      p.parsedAddress?.parity !== "unknown" &&
      p.parsedAddress?.parity !== seedParity
  );

  // Infer street axis vector via PCA over same-parity neighbors within 220m of seed
  const nearbySameSide = sameParityCandidates.filter((p) => {
    if (!p.centroid) return false;
    const [dx, dy] = toFt(p.centroid);
    return Math.hypot(dx, dy) <= 220 * US_SURVEY_FT_PER_METER;
  });

  let axisUx = 1.0;
  let axisUy = 0.0;
  let streetBearingDeg = 0.0;

  if (nearbySameSide.length >= 2) {
    let mx = 0;
    let my = 0;
    const pts = nearbySameSide.map((p) => toFt(p.centroid));
    for (const [x, y] of pts) {
      mx += x;
      my += y;
    }
    mx /= pts.length;
    my /= pts.length;
    let sxx = 0;
    let syy = 0;
    let sxy = 0;
    for (const [x, y] of pts) {
      const dx = x - mx;
      const dy = y - my;
      sxx += dx * dx;
      syy += dy * dy;
      sxy += dx * dy;
    }
    const theta = 0.5 * Math.atan2(2 * sxy, sxx - syy);
    axisUx = Math.cos(theta);
    axisUy = Math.sin(theta);
    streetBearingDeg = segmentBearingDeg(0, 0, axisUx, axisUy);
  } else {
    // Single-lot fallback: pick longest exterior ring axis or cardinal orientation
    const rings = extractOuterRings(seed.geometry);
    if (rings[0] && rings[0].length >= 3) {
      const [ax, ay] = toFt(rings[0][0]);
      const [bx, by] = toFt(rings[0][1]);
      streetBearingDeg = segmentBearingDeg(ax, ay, bx, by);
      axisUx = Math.cos((streetBearingDeg * Math.PI) / 180.0);
      axisUy = Math.sin((streetBearingDeg * Math.PI) / 180.0);
    }
  }

  // Extract contiguous primary blockface halting cleanly at > 16m cross-street ROW gaps
  const primaryContiguous = splitContiguousClusterWithSeed(
    sameParityCandidates,
    seed.hcadNum,
    axisUx,
    axisUy,
    originLng,
    originLat
  );
  const primarySet = new Set(primaryContiguous.map((p) => p.hcadNum));

  const sortedPrimaryByAxis = primaryContiguous.slice().sort((a, b) => {
    const [ax, ay] = toFt(a.centroid);
    const [bx, by] = toFt(b.centroid);
    return ax * axisUx + ay * axisUy - (bx * axisUx + by * axisUy);
  });
  const primaryAxisProjs = sortedPrimaryByAxis.map((p) => {
    const [x, y] = toFt(p.centroid);
    return x * axisUx + y * axisUy;
  });
  const minPrimaryProj = Math.min(...primaryAxisProjs);
  const maxPrimaryProj = Math.max(...primaryAxisProjs);

  // Locate opposing blockface across the street ROW (8m..52m across from primary blockface),
  // restricted to the primary blockface's longitudinal span and clustered contiguously so
  // adjacent 100-block lots across a cross-street intersection do not bleed in.
  const rawOpposing = [];
  const culDeSacMixedCandidates = [];
  for (const op of oppositeParityCandidates) {
    let minGapM = Infinity;
    for (const pr of primaryContiguous) {
      const g = minPolygonGapMeters(op.geometry, pr.geometry);
      if (g < minGapM) minGapM = g;
    }
    if (minGapM <= 5.0) {
      // Directly abutting same-side lot with opposite parity -> cul-de-sac or irregular numbering!
      culDeSacMixedCandidates.push(op);
    } else if (minGapM > 8.0 && minGapM <= 52.0 && op.centroid) {
      const [oxFt, oyFt] = toFt(op.centroid);
      const opProj = oxFt * axisUx + oyFt * axisUy;
      if (opProj >= minPrimaryProj - 45.0 && opProj <= maxPrimaryProj + 45.0) {
        rawOpposing.push(op);
      }
    }
  }
  let opposingContiguous = rawOpposing;
  if (rawOpposing.length > 1) {
    const [seedX, seedY] = toFt(seed.centroid);
    const seedProj = seedX * axisUx + seedY * axisUy;
    const anchorOp = rawOpposing
      .slice()
      .sort((a, b) => {
        const [ax, ay] = toFt(a.centroid);
        const [bx, by] = toFt(b.centroid);
        return (
          Math.abs(ax * axisUx + ay * axisUy - seedProj) -
          Math.abs(bx * axisUx + by * axisUy - seedProj)
        );
      })[0];
    opposingContiguous = splitContiguousClusterWithSeed(
      rawOpposing,
      anchorOp.hcadNum,
      axisUx,
      axisUy,
      originLng,
      originLat
    );
  }

  // Infer the street centerline reference point and strictly orthogonal unit frontNormal
  // perpendicular to (axisUx, axisUy) so unequal end extents never tilt depth projections!
  let streetPointWgs = null;
  let frontNormal = [-axisUy, axisUx];
  if (opposingContiguous.length > 0) {
    let px = 0;
    let py = 0;
    for (const p of primaryContiguous) {
      px += p.centroid[0];
      py += p.centroid[1];
    }
    px /= primaryContiguous.length;
    py /= primaryContiguous.length;
    let ox = 0;
    let oy = 0;
    for (const o of opposingContiguous) {
      ox += o.centroid[0];
      oy += o.centroid[1];
    }
    ox /= opposingContiguous.length;
    oy /= opposingContiguous.length;
    streetPointWgs = [(px + ox) * 0.5, (py + oy) * 0.5];
    const [dxFt, dyFt] = [(ox - px) * ftPerDegLng, (oy - py) * ftPerDegLat];
    if (dxFt * frontNormal[0] + dyFt * frontNormal[1] < 0) {
      frontNormal = [-frontNormal[0], -frontNormal[1]];
    }
  } else {
    // Without opposing lots, detect whether rear-fence neighbors sit on one side (< 3m gap, no street ROW)
    const otherParcels = dedupedPool.filter((p) => !primarySet.has(p.hcadNum) && p.centroid);
    let posTouches = 0;
    let negTouches = 0;
    for (const pr of primaryContiguous) {
      const [prx, pry] = toFt(pr.centroid);
      for (const oth of otherParcels) {
        const g = minPolygonGapMeters(pr.geometry, oth.geometry);
        if (g <= 3.0) {
          const [ox, oy] = toFt(oth.centroid);
          const dot = (ox - prx) * frontNormal[0] + (oy - pry) * frontNormal[1];
          if (Math.abs(dot) > 15.0) {
            if (dot > 0) posTouches += 1;
            else negTouches += 1;
          }
        }
      }
    }
    // Side with direct rear-lot touches (< 3m gap, no street ROW) is the BACK of the blockface!
    if (posTouches > negTouches) {
      frontNormal = [-frontNormal[0], -frontNormal[1]];
    }
  }

  // Detect corner-lot candidates carrying a perpendicular cross-street situs address (DF-5):
  // A corner parcel directly abuts (<= 4.5m gap) either end of the primary blockface along the same front line!
  const endParcels =
    sortedPrimaryByAxis.length >= 2
      ? [sortedPrimaryByAxis[0], sortedPrimaryByAxis[sortedPrimaryByAxis.length - 1]]
      : sortedPrimaryByAxis;
  const meanPerp =
    sortedPrimaryByAxis.reduce((acc, p) => {
      const [x, y] = toFt(p.centroid);
      return acc + (x * frontNormal[0] + y * frontNormal[1]);
    }, 0) / Math.max(1, sortedPrimaryByAxis.length);

  const cornerCandidates = [];
  for (const cand of dedupedPool) {
    if (
      primarySet.has(cand.hcadNum) ||
      opposingContiguous.some((o) => o.hcadNum === cand.hcadNum) ||
      culDeSacMixedCandidates.some((c) => c.hcadNum === cand.hcadNum)
    ) {
      continue;
    }
    if (!cand.parsedAddress?.streetKey || cand.parsedAddress.streetKey === targetStreetKey) continue;
    let abutsEnd = false;
    for (const ep of endParcels) {
      if (minPolygonGapMeters(cand.geometry, ep.geometry) <= 4.5) {
        abutsEnd = true;
        break;
      }
    }
    if (!abutsEnd || !cand.centroid) continue;
    const [cx, cy] = toFt(cand.centroid);
    const signedPerpDeltaFt = cx * frontNormal[0] + cy * frontNormal[1] - meanPerp;
    // Must lie along the same street-facing frontage band ([-38, +58] ft), NOT across the back fence!
    if (signedPerpDeltaFt >= -38.0 && signedPerpDeltaFt <= 58.0) {
      cornerCandidates.push(cand);
    }
  }

  const includeOpposing = Boolean(options.includeOpposingBlockface);
  const includedOverrideMap = options.includedByHcad || {};
  const manualSetbacks = options.manualSetbacksByHcad || {};
  const vacantSfrMap = options.vacantWasSfrByHcad || {};
  const supportSignedMap = options.supportSignedByHcad || {};
  const landUseOverrideMap = options.landUseOverrideByHcad || {};

  const buildRow = (parcel, frontageRole, defaultIncluded, flipNormal = false) => {
    const rowWarnings = [];
    if (parcel.condoUnitCount > 1) {
      rowWarnings.push(
        `Collapsed ${parcel.condoUnitCount} stacked condo unit accounts on this physical lot into 1 parcel.`
      );
    }
    const cls = classifyParcelForChapter42(parcel, {
      vacantWasSfrPast5Years: vacantSfrMap[parcel.hcadNum],
      landUseOverride: landUseOverrideMap[parcel.hcadNum],
    });
    const effectiveRole = cls.landUseCategory === "common_reserve" ? "common_reserve" : frontageRole;
    const setbackCtx = {
      bearingDeg: streetBearingDeg,
      streetPointWgs,
      frontNormal: flipNormal && frontNormal ? [-frontNormal[0], -frontNormal[1]] : frontNormal,
    };
    const sbEst = estimateParcelFrontSetbackFt(parcel, setbackCtx);
    const manualSbRaw = manualSetbacks[parcel.hcadNum];
    const hasManualSb =
      manualSbRaw !== undefined &&
      manualSbRaw !== null &&
      manualSbRaw !== "" &&
      Number.isFinite(Number(manualSbRaw)) &&
      Number(manualSbRaw) > 0;
    const effectiveSetbackFt = hasManualSb ? Number(Number(manualSbRaw).toFixed(1)) : sbEst.setbackFt;
    const setbackSource = hasManualSb ? "manual_override" : sbEst.setbackSource;

    if (effectiveRole === "corner_candidate") {
      rowWarnings.push(
        `Corner parcel addressed on ${parcel.parsedAddress.streetDisplay}; review recorded plat frontage orientation before including.`
      );
    }
    if (effectiveRole === "cul_de_sac") {
      rowWarnings.push("Mixed odd/even parity on contiguous street frontage (cul-de-sac / curve candidate).");
    }

    const explicitInc = includedOverrideMap[parcel.hcadNum];
    const included =
      typeof explicitInc === "boolean"
        ? explicitInc
        : effectiveRole === "common_reserve"
        ? false
        : defaultIncluded;

    return {
      hcadNum: parcel.hcadNum,
      collapsedHcads: parcel.collapsedHcads || [parcel.hcadNum],
      condoUnitCount: parcel.condoUnitCount || 1,
      isSeed: parcel.hcadNum === seed.hcadNum || (parcel.collapsedHcads || []).includes(seed.hcadNum),
      address: parcel.address,
      parsedAddress: parcel.parsedAddress,
      owner: parcel.owner,
      ownerType: parcel.ownerType,
      subdivision: parcel.subdivision,
      legalLines: parcel.legalLines,
      stateClass: parcel.stateClass,
      yearBuilt: parcel.yearBuilt,
      historicDistrict: parcel.historicDistrict,
      lotAreaSqft: parcel.lotAreaSqft,
      areaSource: parcel.areaSource,
      hasStandingStructure: parcel.hasStandingStructure,
      isConstructed: parcel.isConstructed,
      isVacant: parcel.isVacant,
      isSuppressedVacant: parcel.isSuppressedVacant,
      frontageRole: effectiveRole,
      included,
      vacantWasSfrPast5Years: Boolean(vacantSfrMap[parcel.hcadNum]),
      supportSigned: Boolean(supportSignedMap[parcel.hcadNum]),
      manualSetbackFt: hasManualSb ? effectiveSetbackFt : null,
      estimatedSetbackFt: sbEst.setbackFt,
      setbackFt: effectiveSetbackFt,
      setbackSource,
      frontEdgeSegment: sbEst.frontEdgeSegment,
      frontEdgeBearingDeg: sbEst.frontEdgeBearingDeg,
      classification: cls,
      geometry: parcel.geometry,
      centroid: parcel.centroid,
      bbox: parcel.bbox,
      warnings: [...rowWarnings, ...cls.warnings, ...sbEst.warnings],
    };
  };

  const rows = [];
  for (const p of sortedPrimaryByAxis) {
    rows.push(buildRow(p, "interior", true, false));
  }
  for (const c of culDeSacMixedCandidates) {
    const incDefault = Boolean(options.includeCulDeSacMixedParity);
    rows.push(buildRow(c, "cul_de_sac", incDefault, false));
  }
  for (const cn of cornerCandidates) {
    rows.push(buildRow(cn, "corner_candidate", false, false));
  }
  const sortedOpposing = opposingContiguous.slice().sort((a, b) => {
    const [ax, ay] = toFt(a.centroid);
    const [bx, by] = toFt(b.centroid);
    return ax * axisUx + ay * axisUy - (bx * axisUx + by * axisUy);
  });
  for (const op of sortedOpposing) {
    rows.push(buildRow(op, "opposing", includeOpposing, true));
  }

  return {
    seedHcadNum: seed.hcadNum,
    targetStreetKey,
    seedParity,
    hundredBlock: seed.parsedAddress?.hundredBlock,
    streetBearingDeg: Number(streetBearingDeg.toFixed(1)),
    historicDistrict:
      seed.historicDistrict ||
      rows.find((r) => r.included && r.historicDistrict)?.historicDistrict ||
      "",
    includeOpposingBlockface: includeOpposing,
    items: rows,
  };
}

/**
 * Dual-mode Chapter 42 ordinance overlap matcher for blockface lots (DF-3, DF-1).
 * Matches full-lot SMLS/SMBL polygons by interior point / overlap sampling and narrow front-yard
 * SMBL ribbons (< 20 ft area/perimeter ratio) by street-aligned frontage buffer intersection.
 */
export function matchCh42OverlaysToBlockface(
  blockfaceAssembly,
  landUseProtectionFeatures = [],
  refYear = 2026
) {
  const rawItems = Array.isArray(blockfaceAssembly)
    ? blockfaceAssembly
    : Array.isArray(blockfaceAssembly?.items)
    ? blockfaceAssembly.items
    : [];
  const items = rawItems.filter((it) => it && it.included !== false);
  const featureList = Array.isArray(landUseProtectionFeatures)
    ? landUseProtectionFeatures
    : Array.isArray(landUseProtectionFeatures?.features)
    ? landUseProtectionFeatures.features
    : [];
  if (items.length === 0 || featureList.length === 0) return [];

  const matchedByKey = new Map();
  for (const feat of featureList) {
    const props = feat?.properties || {};
    const geom = feat?.geometry;
    const ptype = String(props.protection_type || "").toLowerCase();
    if (!geom || (ptype !== "smls" && ptype !== "smbl")) continue;
    const ovBbox = computeGeometryBbox(geom);
    if (!ovBbox) continue;

    // Quick bbox intersection filter against active blockface parcels
    const candidates = items.filter((it) => {
      const bb = it.bbox;
      return bb && !(bb[2] < ovBbox[0] - 0.0004 || bb[0] > ovBbox[2] + 0.0004 || bb[3] < ovBbox[1] - 0.0004 || bb[1] > ovBbox[3] + 0.0004);
    });
    if (candidates.length === 0) continue;

    // Measure overlay slenderness ratio in Houston survey feet
    const ovAreaSqft = computePolygonAreaSqftPlanar(geom);
    const rings = extractOuterRings(geom);
    const origin = getGeometryReferenceOrigin(geom);
    if (!origin || rings.length === 0) continue;
    const { ftPerDegLng, ftPerDegLat } = getHoustonSurveyFeetScale(origin.refLat);
    const toFt = (pt) => [(pt[0] - origin.refLng) * ftPerDegLng, (pt[1] - origin.refLat) * ftPerDegLat];

    let perimFt = 0;
    let longestBearing = null;
    let longestSeg = 0;
    const ovSegmentsFt = [];
    for (const r of rings) {
      for (let i = 0; i < r.length - 1; i += 1) {
        const [ax, ay] = toFt(r[i]);
        const [bx, by] = toFt(r[i + 1]);
        const len = Math.hypot(bx - ax, by - ay);
        perimFt += len;
        if (len > longestSeg) {
          longestSeg = len;
          longestBearing = segmentBearingDeg(ax, ay, bx, by);
        }
        ovSegmentsFt.push([ax, ay, bx, by]);
      }
    }
    const isRibbon = ptype === "smbl" && ovAreaSqft / Math.max(1, perimFt) < CH42_RIBBON_RATIO_THRESHOLD_FT;

    // Bearing alignment guard: reject ribbons running along a perpendicular cross-street!
    if (
      isRibbon &&
      longestBearing !== null &&
      Number.isFinite(blockfaceAssembly?.streetBearingDeg) &&
      axialAngleDiffDeg(longestBearing, blockfaceAssembly.streetBearingDeg) > 35.0
    ) {
      continue;
    }

    const hitHcads = [];
    for (const it of candidates) {
      if (isRibbon) {
        if (it.frontEdgeSegment) {
          const [fa, fb] = it.frontEdgeSegment;
          const [fax, fay] = toFt(fa);
          const [fbx, fby] = toFt(fb);
          const near = ovSegmentsFt.some(
            ([ox1, oy1, ox2, oy2]) =>
              segmentToSegmentDistFt(fax, fay, fbx, fby, ox1, oy1, ox2, oy2) <= CH42_RIBBON_BUFFER_FT
          );
          if (near) hitHcads.push(it.hcadNum);
        } else if (minPolygonGapMeters(it.geometry, geom) * US_SURVEY_FT_PER_METER <= CH42_RIBBON_BUFFER_FT) {
          hitHcads.push(it.hcadNum);
        }
      } else {
        if (it.centroid && pointInGeometry(it.centroid[0], it.centroid[1], geom, ovBbox)) {
          hitHcads.push(it.hcadNum);
        }
      }
    }

    if (hitHcads.length > 0) {
      const lifecycle = resolveCh42OrdinanceLifecycle(props, refYear);
      const ordNo = String(props.ordinance || props.ORDINANCE_ || props.ORDINANCE || "Unnumbered");
      const key = `${ptype}:${ordNo}`;
      const existing = matchedByKey.get(key);
      if (existing) {
        for (const h of hitHcads) {
          if (!existing.matchedHcads.includes(h)) existing.matchedHcads.push(h);
        }
      } else {
        matchedByKey.set(key, {
          key,
          protectionType: ptype.toUpperCase(),
          ordinance: ordNo,
          name: String(props.name || ""),
          minSqft: Number(props.min_lot_sqft ?? props.min_sqft) || null,
          minFt: Number(props.min_bldg_line_ft ?? props.min_ft) || null,
          isRibbonGeometry: isRibbon,
          matchedHcads: hitHcads,
          lifecycle,
        });
      }
    }
  }
  return Array.from(matchedByKey.values());
}

// ============================================================================
// 7. Exact Scaled-Integer COH Chapter 42 Statutory Calculator (DF-2)
// ============================================================================

/**
 * Evaluates all four distinct City of Houston Chapter 42 statutory formulas over an assembled
 * blockface using exact scaled-integer cross-multiplication (avoiding IEEE-754 float drift).
 */
export function computeChapter42BlockEligibility(blockfaceItems = [], options = {}) {
  const historicDistrictName = String(options.historicDistrict || "").trim();
  const isHistoricDistrict =
    typeof options.isHistoricDistrict === "boolean"
      ? options.isHistoricDistrict
      : Boolean(historicDistrictName);
  const thresholdPct = isHistoricDistrict ? 60 : 70;

  const activeItems = [];
  const excludedItems = [];
  const segregatedReserves = [];

  for (const raw of blockfaceItems || []) {
    if (!raw) continue;
    const ov = {
      vacantWasSfrPast5Years:
        options.vacantWasSfrByHcad?.[raw.hcadNum] ?? raw.vacantWasSfrPast5Years ?? false,
      landUseOverride: options.landUseOverrideByHcad?.[raw.hcadNum] ?? raw.landUseOverride ?? "",
    };
    const cls =
      ov.landUseOverride || raw.vacantWasSfrPast5Years !== ov.vacantWasSfrPast5Years || !raw.classification
        ? classifyParcelForChapter42(raw, ov)
        : raw.classification;

    const manualSbRaw = options.manualSetbacksByHcad?.[raw.hcadNum] ?? raw.manualSetbackFt;
    const hasManualSb =
      manualSbRaw !== undefined &&
      manualSbRaw !== null &&
      manualSbRaw !== "" &&
      Number.isFinite(Number(manualSbRaw)) &&
      Number(manualSbRaw) > 0;
    const effectiveSetbackFt = hasManualSb
      ? Number(Number(manualSbRaw).toFixed(1))
      : Number.isFinite(Number(raw.setbackFt)) && Number(raw.setbackFt) > 0
      ? Number(Number(raw.setbackFt).toFixed(1))
      : null;

    const included =
      options.includedByHcad && raw.hcadNum in options.includedByHcad
        ? Boolean(options.includedByHcad[raw.hcadNum])
        : raw.included !== false;
    const supportSigned =
      options.supportSignedByHcad && raw.hcadNum in options.supportSignedByHcad
        ? Boolean(options.supportSignedByHcad[raw.hcadNum])
        : Boolean(raw.supportSigned);

    const enriched = {
      ...raw,
      included,
      supportSigned,
      vacantWasSfrPast5Years: Boolean(ov.vacantWasSfrPast5Years),
      classification: cls,
      effectiveSetbackFt,
      setbackSource: hasManualSb ? "manual_override" : raw.setbackSource || "unavailable",
      lotSqftInt: Math.max(0, Math.round(Number(raw.lotAreaSqft) || 0)),
      setbackTenthsFtInt:
        effectiveSetbackFt !== null ? Math.max(1, Math.round(effectiveSetbackFt * 10)) : null,
    };

    if (cls.landUseCategory === "common_reserve" && !included) {
      segregatedReserves.push(enriched);
    } else if (included) {
      activeItems.push(enriched);
    } else {
      excludedItems.push(enriched);
    }
  }

  // Universe 1: Sec. 42-197(b)(4) & Sec. 42-170(b)(4) Single-Family Eligibility Gate
  const eligibilityLots = activeItems.filter((it) => it.classification.inSfrDenominator);
  const sfrLots = eligibilityLots.filter((it) => it.classification.isSfrNumerator);
  const eligDenom = eligibilityLots.length;
  const sfrCount = sfrLots.length;
  const passesBlock60Pct = eligDenom > 0 && sfrCount * 100 >= 60 * eligDenom;
  const passesArea80Pct = eligDenom > 0 && sfrCount * 100 >= 80 * eligDenom;
  const sfrPctDisplay = eligDenom > 0 ? Number(((sfrCount / eligDenom) * 100).toFixed(1)) : 0;

  // Universe 2: Sec. 42-202 Special Minimum Lot Size (SMLS)
  const smlsQualifyingLots = activeItems
    .filter((it) => it.classification.inSmlsUniverse && it.lotSqftInt > 0)
    .slice()
    .sort((a, b) => b.lotSqftInt - a.lotSqftInt || a.hcadNum.localeCompare(b.hcadNum));
  const totalSmlsSqftInt = smlsQualifyingLots.reduce((acc, it) => acc + it.lotSqftInt, 0);
  let cumSqftInt = 0;
  let smlsCutIndex = -1;
  const smlsRankedRows = smlsQualifyingLots.map((it, idx) => {
    cumSqftInt += it.lotSqftInt;
    const reachesThreshold =
      totalSmlsSqftInt > 0 && cumSqftInt * 100 >= thresholdPct * totalSmlsSqftInt;
    const isCutoffLot = reachesThreshold && smlsCutIndex === -1;
    if (isCutoffLot) smlsCutIndex = idx;
    return {
      rank: idx + 1,
      hcadNum: it.hcadNum,
      address: it.address,
      lotSqftInt: it.lotSqftInt,
      isVacant: it.isVacant,
      sharePct: totalSmlsSqftInt > 0 ? Number(((it.lotSqftInt / totalSmlsSqftInt) * 100).toFixed(2)) : 0,
      cumulativeSqftInt: cumSqftInt,
      cumulativePct: totalSmlsSqftInt > 0 ? Number(((cumSqftInt / totalSmlsSqftInt) * 100).toFixed(2)) : 0,
      isCutoffLot,
    };
  });
  const effectiveMinLotSqft =
    smlsCutIndex >= 0 ? smlsQualifyingLots[smlsCutIndex].lotSqftInt : null;

  // Universe 3: Sec. 42-173 Special Minimum Building Line (SMBL) across CONSTRUCTED buildings only
  const smblEligibleConstructed = activeItems.filter(
    (it) =>
      it.classification.inSmblUniverse &&
      it.hasStandingStructure &&
      !it.isVacant &&
      !it.isSuppressedVacant
  );
  const missingFootprintConstructedCount = smblEligibleConstructed.filter(
    (it) => it.setbackTenthsFtInt === null
  ).length;
  const excludedVacantOrDemolishedCount = activeItems.filter(
    (it) => it.isVacant || it.isSuppressedVacant || !it.hasStandingStructure
  ).length;

  const smblConstructedLots = smblEligibleConstructed
    .filter((it) => it.setbackTenthsFtInt !== null)
    .slice()
    .sort(
      (a, b) => b.setbackTenthsFtInt - a.setbackTenthsFtInt || a.hcadNum.localeCompare(b.hcadNum)
    );
  const totalTenthsFtInt = smblConstructedLots.reduce((acc, it) => acc + it.setbackTenthsFtInt, 0);
  let cumTenthsFtInt = 0;
  let smblCutIndex = -1;
  const smblRankedRows = smblConstructedLots.map((it, idx) => {
    cumTenthsFtInt += it.setbackTenthsFtInt;
    const reachesThreshold =
      totalTenthsFtInt > 0 && cumTenthsFtInt * 100 >= thresholdPct * totalTenthsFtInt;
    const isCutoffLot = reachesThreshold && smblCutIndex === -1;
    if (isCutoffLot) smblCutIndex = idx;
    return {
      rank: idx + 1,
      hcadNum: it.hcadNum,
      address: it.address,
      setbackFt: it.setbackTenthsFtInt / 10,
      setbackSource: it.setbackSource,
      sharePct:
        totalTenthsFtInt > 0
          ? Number(((it.setbackTenthsFtInt / totalTenthsFtInt) * 100).toFixed(2))
          : 0,
      cumulativeFt: Number((cumTenthsFtInt / 10).toFixed(1)),
      cumulativePct:
        totalTenthsFtInt > 0 ? Number(((cumTenthsFtInt / totalTenthsFtInt) * 100).toFixed(2)) : 0,
      isCutoffLot,
    };
  });
  const effectiveMinSetbackFt =
    smblCutIndex >= 0 ? smblConstructedLots[smblCutIndex].setbackTenthsFtInt / 10 : null;

  // Universe 4: Sec. 42-198..207 / Sec. 42-171..178 Organizer Petition & Fast-Track Land Area
  const petitionLots = activeItems.filter(
    (it) => it.classification.inPetitionAreaUniverse && it.lotSqftInt > 0
  );
  const petitionTotalSqftInt = petitionLots.reduce((acc, it) => acc + it.lotSqftInt, 0);
  const signedLots = petitionLots.filter((it) => it.supportSigned);
  const signedSqftInt = signedLots.reduce((acc, it) => acc + it.lotSqftInt, 0);
  const directToCouncilArea51PctSqft =
    petitionTotalSqftInt > 0 ? Math.ceil((petitionTotalSqftInt * 51) / 100) : 0;
  const meetsDirectToCouncilFastTrack =
    petitionTotalSqftInt > 0 && signedSqftInt * 100 >= 51 * petitionTotalSqftInt;
  const areaAppMinOwners10Pct = Math.max(1, Math.ceil(petitionLots.length * 0.1));
  const areaBallotSupport55Pct = Math.ceil(petitionLots.length * 0.55);
  const rescissionArea67PctSqft =
    petitionTotalSqftInt > 0 ? Math.ceil((petitionTotalSqftInt * 67) / 100) : 0;

  // Ownership composition & townhome-subdivision vulnerability metrics
  let individualOwners = 0;
  let familyTrustOwners = 0;
  let corporateOwners = 0;
  let townhomeSplitVulnerableLots = 0;
  const splitMinThreshold = effectiveMinLotSqft
    ? Math.max(3500, Math.round(effectiveMinLotSqft * 1.4))
    : 5000;

  for (const it of petitionLots) {
    if (it.ownerType === "corporate") corporateOwners += 1;
    else if (it.ownerType === "family_trust") familyTrustOwners += 1;
    else individualOwners += 1;

    // Without SMLS, COH urban lot standard allows splitting standard 5,000+ sq ft lots into 1,400-3,500 sq ft townhome flag/shared-drive lots
    if (it.lotSqftInt >= 3500 && (!effectiveMinLotSqft || it.lotSqftInt >= Math.min(5000, splitMinThreshold))) {
      townhomeSplitVulnerableLots += 1;
    }
  }

  return {
    isHistoricDistrict,
    historicDistrictName,
    thresholdPct,
    activeLotCount: activeItems.length,
    excludedLotCount: excludedItems.length,
    segregatedReserveCount: segregatedReserves.length,
    items: activeItems,
    excludedItems,
    segregatedReserves,
    eligibilityCheck: {
      sfrCount,
      denominatorCount: eligDenom,
      excludedCivicReligiousCount: activeItems.length - eligDenom,
      sfrPct: sfrPctDisplay,
      passesBlock60Pct,
      passesArea80Pct,
    },
    smlsResult: {
      universeCount: smlsQualifyingLots.length,
      totalAreaSqft: totalSmlsSqftInt,
      thresholdPct,
      effectiveMinLotSqft,
      cutIndex: smlsCutIndex,
      rankedRows: smlsRankedRows,
    },
    smblResult: {
      constructedLotCount: smblConstructedLots.length,
      totalBuildingLineFt: Number((totalTenthsFtInt / 10).toFixed(1)),
      excludedVacantOrDemolishedCount,
      missingFootprintConstructedCount,
      thresholdPct,
      effectiveMinSetbackFt,
      cutIndex: smblCutIndex,
      rankedRows: smblRankedRows,
    },
    petitionThresholds: {
      qualifyingLotCount: petitionLots.length,
      qualifyingLandAreaSqft: petitionTotalSqftInt,
      blockAppMinOwners: 1,
      areaAppMinOwners10Pct,
      directToCouncilArea51PctSqft,
      signedOwnerCount: signedLots.length,
      signedLandAreaSqft: signedSqftInt,
      signedLandAreaPct:
        petitionTotalSqftInt > 0
          ? Number(((signedSqftInt / petitionTotalSqftInt) * 100).toFixed(1))
          : 0,
      remainingFastTrackSqft: Math.max(0, directToCouncilArea51PctSqft - signedSqftInt),
      meetsDirectToCouncilFastTrack,
      areaBallotSupport55Pct,
      rescissionArea67PctSqft,
      townhomeSplitVulnerableLots,
      ownershipBreakdown: {
        individualOwners,
        familyTrustOwners,
        corporateOwners,
      },
    },
  };
}

/**
 * Builds a MapLibre GeoJSON FeatureCollection coloring candidate blockface parcels by role.
 */
export function buildChapter42BlockfaceGeoJson(assembly, calcResult = null) {
  const includedMap = new Map();
  if (calcResult?.items) {
    for (const it of calcResult.items) includedMap.set(it.hcadNum, it);
  }
  const cutoffSmlsHcad =
    calcResult?.smlsResult?.cutIndex >= 0
      ? calcResult.smlsResult.rankedRows[calcResult.smlsResult.cutIndex]?.hcadNum
      : "";
  const features = [];
  for (const raw of assembly?.items || []) {
    if (!raw?.geometry) continue;
    const active = includedMap.get(raw.hcadNum);
    const isInc = Boolean(active);
    let roleKey = "excluded";
    if (isInc) {
      if (raw.isSeed) roleKey = "seed";
      else if (raw.hcadNum === cutoffSmlsHcad) roleKey = "cutoff";
      else if (raw.frontageRole === "opposing") roleKey = "opposing_included";
      else roleKey = "included";
    } else if (raw.frontageRole === "corner_candidate") {
      roleKey = "corner_candidate";
    } else if (raw.frontageRole === "opposing") {
      roleKey = "opposing_candidate";
    } else if (raw.frontageRole === "common_reserve") {
      roleKey = "common_reserve";
    }
    features.push({
      type: "Feature",
      geometry: raw.geometry,
      properties: {
        hcad_num: raw.hcadNum,
        address: raw.address || "",
        role: roleKey,
        [CH42_BLOCKFACE_ROLE_PROP]: roleKey,
        included: isInc,
        lot_sqft: Math.round(raw.lotAreaSqft || 0),
        setback_ft: active?.effectiveSetbackFt ?? raw.setbackFt ?? null,
      },
    });
  }
  return { type: "FeatureCollection", features };
}

// ============================================================================
// 8. Pure HTML Presentation Renderers (Scorecard Hub, Inline Card & Ch. 42 Kit)
// ============================================================================

export function escapeOrganizerHtml(val) {
  return String(val ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function fmtInt(n) {
  return Number.isFinite(Number(n)) ? Math.round(Number(n)).toLocaleString("en-US") : "0";
}

/**
 * Renders the compact Preservation Scorecard & Vulnerability summary card injected directly
 * inside the Boundary Inspector drawer (#inspector-body).
 */
export function renderBoundaryInlineScorecardHtml(scorecard, liveThreats = null) {
  if (!scorecard) return "";
  const threatCount = liveThreats?.totalUniqueThreats || 0;
  const cm = scorecard.councilMemberMeta;
  const expCount = scorecard.expiringOrdinances?.length || 0;

  return `
    <div class="detail-card boundary-scorecard-inline-card" data-scorecard-key="${escapeOrganizerHtml(scorecard.key)}">
      <div class="detail-card-title" style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap">
        <span>Preservation Scorecard &amp; Chapter 42 Armor</span>
        ${
          threatCount > 0
            ? `<span class="badge badge-danger" style="font-size:0.7rem">${fmtInt(threatCount)} Live Alert${threatCount === 1 ? "" : "s"}</span>`
            : `<span class="badge badge-muted" style="font-size:0.7rem">0 Active Alerts</span>`
        }
      </div>
      ${
        cm
          ? `<div class="scorecard-council-strip">
               <strong>Council Member ${escapeOrganizerHtml(cm.member)}</strong> (District ${escapeOrganizerHtml(cm.letter)}, term through Jan 2028) &middot;
               <a href="tel:${escapeOrganizerHtml(cm.phone)}" class="inline-ext-link">${escapeOrganizerHtml(cm.phone)}</a> &middot;
               <a href="mailto:${escapeOrganizerHtml(cm.email)}" class="inline-ext-link">${escapeOrganizerHtml(cm.email)}</a> &middot;
               <a href="${escapeOrganizerHtml(cm.url)}" target="_blank" rel="noopener noreferrer" class="inline-ext-link">District Office &#8599;</a>
             </div>`
          : scorecard.councilDistricts
          ? `<div style="font-size:0.76rem;color:var(--text-secondary);margin-bottom:8px">
               City Council District${scorecard.councilDistricts.includes(",") ? "s" : ""}: <strong>${escapeOrganizerHtml(scorecard.councilDistricts)}</strong>
               ${scorecard.superNeighborhood ? ` &middot; Super Nbhd: <strong>${escapeOrganizerHtml(scorecard.superNeighborhood)}</strong>` : ""}
             </div>`
          : ""
      }
      <div class="scorecard-inline-kpi-grid">
        <div class="scorecard-kpi-cell">
          <div class="scorecard-kpi-val">${fmtInt(scorecard.pre1940Count)} <span class="scorecard-kpi-pct">(${scorecard.pre1940Pct}%)</span></div>
          <div class="scorecard-kpi-lbl">Pre-1940 Buildings (of ${fmtInt(scorecard.buildingCount)})</div>
        </div>
        <div class="scorecard-kpi-cell">
          <div class="scorecard-kpi-val">${scorecard.ch42ProtectedPct}%</div>
          <div class="scorecard-kpi-lbl">Ch. 42 Protected Res. Lots (${fmtInt(scorecard.ch42ProtectedCount)}/${fmtInt(scorecard.residentialParcelCount)})</div>
        </div>
        <div class="scorecard-kpi-cell">
          <div class="scorecard-kpi-val">${scorecard.ownerOccupiedPct}% / ${scorecard.corporateLlcPct}%</div>
          <div class="scorecard-kpi-lbl">Resident-Occ. vs. Corp/LLC Lots</div>
        </div>
        <div class="scorecard-kpi-cell">
          <div class="scorecard-kpi-val">${fmtInt(scorecard.demolitionsTotal2020To2026)}</div>
          <div class="scorecard-kpi-lbl">2020-2026 YTD Teardowns (${fmtInt(scorecard.post2020InfillCount)} new builds)</div>
        </div>
      </div>
      ${
        expCount > 0
          ? `<div class="scorecard-expiring-callout">
               <strong>Renewal Watch (2026-2031):</strong> ${fmtInt(expCount)} Chapter 42 ordinance${expCount === 1 ? "" : "s"} (${fmtInt(scorecard.expiringCh42ParcelCount)} residential lots) reach their 20-year sunset by 2031 under Sec. 42-207(a) / Sec. 42-178(a).
             </div>`
          : ""
      }
      <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:10px">
        <button type="button" class="btn-secondary btn-open-full-scorecard"
          data-scorecard-layer="${escapeOrganizerHtml(scorecard.layer)}"
          data-scorecard-id="${escapeOrganizerHtml(scorecard.id)}"
          style="flex:1 1 auto;font-weight:600">
          Open Full Preservation Scorecard &amp; Print Brief
        </button>
      </div>
    </div>
  `;
}

export function renderScorecardHubTableRowsHtml(records = [], selectedKey = "") {
  if (!Array.isArray(records) || records.length === 0) {
    return `<tr><td colspan="7" style="text-align:center;padding:18px;color:var(--text-secondary)">No boundaries match your current search/filter.</td></tr>`;
  }
  return records
    .map((r) => {
      const isSel = r.key === selectedKey;
      const cmNote = r.councilMemberMeta
        ? `CM ${r.councilMemberMeta.member}`
        : r.alreadyDesignatedCohHd
        ? "Now Designated COH HD"
        : r.superNeighborhood || r.typeLabel;
      return `
        <tr class="scorecard-hub-row${isSel ? " is-selected" : ""}"
            data-scorecard-layer="${escapeOrganizerHtml(r.layer)}"
            data-scorecard-id="${escapeOrganizerHtml(r.id)}"
            tabindex="0">
          <td>
            <div style="font-weight:600;color:var(--text-primary)">${escapeOrganizerHtml(r.name)}</div>
            <div style="font-size:0.72rem;color:var(--text-secondary)">${escapeOrganizerHtml(cmNote)}${r.councilDistricts && !r.councilMemberMeta ? ` &middot; Dist. ${escapeOrganizerHtml(r.councilDistricts)}` : ""}</div>
          </td>
          <td style="text-align:right">${fmtInt(r.buildingCount)}</td>
          <td style="text-align:right"><strong>${fmtInt(r.pre1940Count)}</strong> <span style="color:var(--text-secondary);font-size:0.72rem">(${r.pre1940Pct}%)</span></td>
          <td style="text-align:right">${r.cohChapter33Pct}% / <strong>${r.ch42ProtectedPct}%</strong></td>
          <td style="text-align:right">${r.expiringOrdinances.length > 0 ? `<span class="scorecard-warn-pill">${fmtInt(r.expiringOrdinances.length)} (${fmtInt(r.expiringCh42ParcelCount)} lots)</span>` : `<span style="color:var(--text-muted)">0</span>`}</td>
          <td style="text-align:right">${r.ownerOccupiedPct}% / ${r.corporateLlcPct}%</td>
          <td style="text-align:right"><strong>${fmtInt(r.demolitionsTotal2020To2026)}</strong></td>
        </tr>
      `;
    })
    .join("");
}

/**
 * Renders the full Preservation Scorecard Detail & Printable 1-Page Council / Civic Club Brief.
 */
export function renderScorecardDetailHtml(scorecard, liveThreats = null, options = {}) {
  if (!scorecard) {
    return `<div style="padding:24px;color:var(--text-secondary)">Select a Council District, Historic District, Super Neighborhood, or Subdivision on the left to inspect its Preservation Scorecard.</div>`;
  }
  const shareUrl = String(options.shareUrl || "");
  const asOfLabel = String(options.generatedAt || "October 2026");
  const maxDecCount = Math.max(1, ...scorecard.decadeHistogram.map((d) => d.count));
  const maxDemoCount = Math.max(1, ...scorecard.demolitionsByYear.map((d) => d.count));
  const threats = liveThreats?.items || [];
  const cm = scorecard.councilMemberMeta;

  const histBarsHtml = scorecard.decadeHistogram
    .filter((d) => d.decade >= 1860 || d.count > 0)
    .map((d) => {
      const hPct = Math.max(d.count > 0 ? 5 : 0, Math.round((d.count / maxDecCount) * 100));
      const isHist = d.decade < 1940;
      return `
        <div class="sc-bar-col" title="${d.decade}s: ${fmtInt(d.count)} standing structures">
          <div class="sc-bar-track">
            <div class="sc-bar-fill${isHist ? " is-pre40" : ""}" style="height:${hPct}%"></div>
          </div>
          <div class="sc-bar-lbl">${String(d.decade).slice(2)}s</div>
        </div>
      `;
    })
    .join("");

  const demoBarsHtml = scorecard.demolitionsByYear
    .map((d) => {
      const hPct = Math.max(d.count > 0 ? 6 : 0, Math.round((d.count / maxDemoCount) * 100));
      return `
        <div class="sc-demo-col" title="${d.year}${d.isYtd ? " YTD" : ""}: ${fmtInt(d.count)} structural teardown parcels">
          <div class="sc-demo-num">${fmtInt(d.count)}</div>
          <div class="sc-bar-track sc-bar-track-sm">
            <div class="sc-bar-fill is-demo" style="height:${hPct}%"></div>
          </div>
          <div class="sc-bar-lbl">${d.year}${d.isYtd ? "*" : ""}</div>
        </div>
      `;
    })
    .join("");

  return `
    <div class="scorecard-detail-sheet" data-scorecard-key="${escapeOrganizerHtml(scorecard.key)}">
      <div class="scorecard-detail-header">
        <div>
          <div class="scorecard-eyebrow">
            <span>${escapeOrganizerHtml(scorecard.typeLabel)}</span>
            ${scorecard.alreadyDesignatedCohHd ? `<span class="badge badge-accent">Designated COH Historic District</span>` : ""}
            ${scorecard.areaAcres > 0 ? `<span>&middot; ${scorecard.areaAcres.toLocaleString("en-US")} acres</span>` : ""}
            ${scorecard.councilDistricts && !cm ? `<span>&middot; Council District ${escapeOrganizerHtml(scorecard.councilDistricts)}</span>` : ""}
            ${scorecard.superNeighborhood ? `<span>&middot; ${escapeOrganizerHtml(scorecard.superNeighborhood)}</span>` : ""}
          </div>
          <h3 class="scorecard-detail-title">${escapeOrganizerHtml(scorecard.name)}</h3>
          ${
            cm
              ? `<div class="scorecard-cm-banner">
                   <div>
                     <strong>Council Member ${escapeOrganizerHtml(cm.member)}</strong> &middot; District ${escapeOrganizerHtml(cm.letter)} (Current term through January 2028)
                   </div>
                   <div style="display:flex;flex-wrap:wrap;gap:10px;font-size:0.78rem">
                     <a href="tel:${escapeOrganizerHtml(cm.phone)}" class="inline-ext-link">${escapeOrganizerHtml(cm.phone)}</a>
                     <a href="mailto:${escapeOrganizerHtml(cm.email)}" class="inline-ext-link">${escapeOrganizerHtml(cm.email)}</a>
                     <a href="${escapeOrganizerHtml(cm.url)}" target="_blank" rel="noopener noreferrer" class="inline-ext-link">Official Council Page &#8599;</a>
                   </div>
                 </div>`
              : ""
          }
          ${
            scorecard.civicClub
              ? `<div style="font-size:0.78rem;color:var(--text-secondary);margin-top:4px">Registered Civic Association: <strong>${escapeOrganizerHtml(scorecard.civicClub)}</strong></div>`
              : ""
          }
          ${
            scorecard.platMeta?.citation
              ? `<div style="font-size:0.76rem;color:var(--text-secondary);margin-top:4px">
                   Plat Era: <strong>${escapeOrganizerHtml(scorecard.platMeta.year_label || "Historic")}</strong> &middot;
                   ${escapeOrganizerHtml(scorecard.platMeta.citation)}
                   ${scorecard.platMeta.deed_citation ? ` &middot; Deed Ref: ${escapeOrganizerHtml(scorecard.platMeta.deed_citation)}` : ""}
                 </div>`
              : ""
          }
          ${
            scorecard.holcMeta?.grade
              ? `<div style="font-size:0.76rem;color:var(--text-secondary);margin-top:4px">
                   1937 HOLC Grade <strong>${escapeOrganizerHtml(scorecard.holcMeta.grade)}</strong> (${escapeOrganizerHtml(scorecard.holcMeta.code)})
                   ${Number.isFinite(Number(scorecard.holcMeta.temp_f)) ? ` &middot; Afternoon Heat Index: <strong>${Number(scorecard.holcMeta.temp_f).toFixed(1)}&deg;F</strong> (${Number(scorecard.holcMeta.delta_vs_a) >= 0 ? "+" : ""}${Number(scorecard.holcMeta.delta_vs_a).toFixed(1)}&deg;F vs. Grade A)` : ""}
                 </div>`
              : ""
          }
        </div>
        <div class="scorecard-detail-actions no-print">
          <button type="button" class="btn-secondary btn-scorecard-fly"
            data-scorecard-layer="${escapeOrganizerHtml(scorecard.layer)}"
            data-scorecard-id="${escapeOrganizerHtml(scorecard.id)}">
            Focus on Map
          </button>
          <button type="button" class="btn-secondary btn-scorecard-ch42-probe"
            data-lng="${scorecard.centerLng}" data-lat="${scorecard.centerLat}">
            Launch Ch. 42 Calculator Here
          </button>
          <button type="button" class="btn-secondary btn-scorecard-copy-link"
            data-share-url="${escapeOrganizerHtml(shareUrl)}">
            Copy Scorecard Link
          </button>
          <button type="button" class="btn-primary btn-scorecard-print">
            Print 1-Page Brief
          </button>
        </div>
      </div>

      <div class="scorecard-metrics-grid">
        <div class="scorecard-stat-box">
          <div class="sc-stat-label">Standing Historic Fabric</div>
          <div class="sc-stat-big">${fmtInt(scorecard.pre1940Count)} <span class="sc-stat-sub">pre-1940 (${scorecard.pre1940Pct}%)</span></div>
          <div class="sc-stat-meta">
            ${fmtInt(scorecard.buildingCount)} total structures &middot; Median ${scorecard.medianYear || "N/A"} &middot; Earliest ${scorecard.earliestYear || "N/A"}<br/>
            Pre-1920: <strong>${fmtInt(scorecard.pre1920Count)}</strong> &middot; Pre-1975 (NRHP 50-yr age): <strong>${fmtInt(scorecard.pre1975Count)}</strong> (${scorecard.pre1975Pct}%)
          </div>
        </div>

        <div class="scorecard-stat-box">
          <div class="sc-stat-label">Legal Protection Gap</div>
          <div class="sc-stat-big">${scorecard.ch42ProtectedPct}% <span class="sc-stat-sub">Ch. 42 lot armor</span></div>
          <div class="sc-stat-meta">
            COH Ch. 33 District coverage: <strong>${scorecard.cohChapter33Pct}%</strong> (${fmtInt(scorecard.cohDistrictProtectedBuildings)} bldgs)<br/>
            Ch. 42 protected res. lots: <strong>${fmtInt(scorecard.ch42ProtectedCount)}</strong> of ${fmtInt(scorecard.residentialParcelCount)} (SMLS: ${fmtInt(scorecard.smlsOrdinanceCount)} ord. / SMBL: ${fmtInt(scorecard.smblOrdinanceCount)} ord.)<br/>
            Protected Landmarks: <strong>${fmtInt(scorecard.protectedLandmarkCount)}</strong> &middot; 90-Day Waiver LMs: <strong>${fmtInt(scorecard.waiverLandmarkCount)}</strong> &middot; Good Brick: <strong>${fmtInt(scorecard.goodBrickCount)}</strong>
          </div>
        </div>

        <div class="scorecard-stat-box">
          <div class="sc-stat-label">Ownership Stability (HCAD Parcels)</div>
          <div class="sc-stat-big">${scorecard.ownerOccupiedPct}% <span class="sc-stat-sub">resident situs-matched</span></div>
          <div class="sc-stat-meta">
            Resident homestead/trust: <strong>${fmtInt(scorecard.ownerOccupiedCount)}</strong> &middot; Family trusts/estates: <strong>${fmtInt(scorecard.familyTrustCount)}</strong> (${scorecard.familyTrustPct}%)<br/>
            Corporate LLC/LP/Inc: <strong>${fmtInt(scorecard.corporateLlcCount)}</strong> (${scorecard.corporateLlcPct}%) &middot; Absentee ind.: <strong>${fmtInt(scorecard.absenteeIndividualCount)}</strong> (${scorecard.absenteeIndividualPct}%)
          </div>
        </div>

        <div class="scorecard-stat-box">
          <div class="sc-stat-label">2020-2026 Teardown &amp; Infill Velocity</div>
          <div class="sc-stat-big">${fmtInt(scorecard.demolitionsTotal2020To2026)} <span class="sc-stat-sub">teardown parcels</span></div>
          <div class="sc-stat-meta">
            Matched to pre-1940 parcel history: <strong>${fmtInt(scorecard.demolitionsPre1940Count)}</strong><br/>
            Post-2020 new construction footprints: <strong>${fmtInt(scorecard.post2020InfillCount)}</strong> &middot; Documented Lost Houston sites: <strong>${fmtInt(scorecard.lostHoustonCount)}</strong>
          </div>
        </div>
      </div>

      <div class="scorecard-charts-row">
        <div class="scorecard-chart-panel">
          <div class="sc-panel-title">Standing Structures by Construction Decade</div>
          <div class="sc-bars-wrap">${histBarsHtml}</div>
        </div>
        <div class="scorecard-chart-panel">
          <div class="sc-panel-title">Annual Main-Structure Demolitions (2020-2026 YTD*)</div>
          <div class="sc-bars-wrap sc-demo-wrap">${demoBarsHtml}</div>
        </div>
      </div>

      ${
        scorecard.expiringOrdinances.length > 0
          ? `<div class="scorecard-section-block scorecard-renewal-box">
               <div class="sc-panel-title">Action Alert: Chapter 42 Ordinances Reaching 20-Year Sunset (2026-2031)</div>
               <p style="margin:0 0 8px;font-size:0.78rem;color:var(--text-secondary)">
                 Pre-July 24, 2015 SMLS and SMBL ordinances expire after 20 years under COH Code Sec. 42-207(a) and Sec. 42-178(a) (${fmtInt(scorecard.expiringCh42ParcelCount)} residential parcels in this boundary). Civic clubs should contact COH Planning at <strong>${escapeOrganizerHtml(COH_CHAPTER_42_RESOURCES.planningIntakePhone)}</strong> before expiration to renew for a 40-year term:
               </p>
               <div class="sc-ord-pill-list">
                 ${scorecard.expiringOrdinances
                   .map(
                     (o) =>
                       `<span class="sc-ord-pill">${escapeOrganizerHtml(o.protectionType)} Ord. ${escapeOrganizerHtml(o.ordinance)} &middot; ${o.isEstimatedSunset ? "Est. " : ""}${o.expirationYear}</span>`
                   )
                   .join("")}
               </div>
             </div>`
          : ""
      }

      <div class="scorecard-section-block">
        <div class="sc-panel-title">
          Active Preservation Early-Warning Alerts Inside Boundary (${fmtInt(threats.length)} deduplicated propert${threats.length === 1 ? "y" : "ies"})
        </div>
        ${
          threats.length === 0
            ? `<div style="font-size:0.78rem;color:var(--text-secondary)">Zero active demolition/sewer disconnect permits, Tier 1/2 replat submittals, active watchlist escalations, or pending HAHC demolition dockets inside this boundary as of ${escapeOrganizerHtml(asOfLabel)}.</div>`
            : `<div class="scorecard-threats-list">
                 ${threats
                   .slice(0, 20)
                   .map(
                     (t) => `
                   <div class="sc-threat-item">
                     <div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap">
                       <div>
                         <strong>${escapeOrganizerHtml(t.title)}</strong>
                         ${t.address && t.address !== t.title ? `<span style="color:var(--text-secondary)"> &middot; ${escapeOrganizerHtml(t.address)}</span>` : ""}
                         ${t.yearBuilt ? `<span class="sc-yr-tag">Built ${t.yearBuilt}</span>` : ""}
                       </div>
                       <div style="display:flex;gap:4px;flex-wrap:wrap">
                         ${(t.badges || []).map((b) => `<span class="sc-threat-badge">${escapeOrganizerHtml(b)}</span>`).join("")}
                       </div>
                     </div>
                     <div style="font-size:0.75rem;color:var(--text-secondary);margin-top:3px">
                       ${escapeOrganizerHtml((t.details || [])[0]?.summary || "")}
                       ${
                         t.hcadNum
                           ? ` <button type="button" class="btn-link-inline no-print btn-sc-jump-ch42" data-hcad="${escapeOrganizerHtml(t.hcadNum)}">Analyze Blockface (Ch. 42)</button>`
                           : ""
                       }
                     </div>
                   </div>`
                   )
                   .join("")}
               </div>`
        }
      </div>

      <div class="scorecard-print-footer">
        Source: Preservation Houston &amp; The Houston Building Atlas (${escapeOrganizerHtml(asOfLabel)}) &middot; Reconciled across 1.51M building footprints, 1.55M HCAD parcels, COH Planning GIS, COH Demolition Permits (*2026 YTD through Oct 2026), Planning Commission Plats, and HAHC Dockets.
        ${shareUrl ? `<br/>Interactive Live Link: <span>${escapeOrganizerHtml(shareUrl)}</span>` : ""}
      </div>
    </div>
  `;
}

/**
 * Renders the complete Chapter 42 Block-Face Eligibility Calculator & Printable Civic Club
 * Pre-Submittal / Walk-Sheet Packet inside #ch42-modal.
 */
export function renderChapter42ModalHtml(assembly, calc, matchedOverlays = [], options = {}) {
  if (!assembly || !calc) {
    return `<div style="padding:24px;color:var(--text-secondary)">Loading live HCAD blockface parcels and building footprints...</div>`;
  }
  const shareUrl = String(options.shareUrl || "");
  const elig = calc.eligibilityCheck;
  const smls = calc.smlsResult;
  const smbl = calc.smblResult;
  const pet = calc.petitionThresholds;
  const expiringMatches = matchedOverlays.filter((m) => m.lifecycle?.sunset_status === "expiring_soon");

  const smlsRankByHcad = new Map(smls.rankedRows.map((r) => [r.hcadNum, r]));
  const smblRankByHcad = new Map(smbl.rankedRows.map((r) => [r.hcadNum, r]));

  const allDisplayRows = assembly.items.map((raw) => {
    const active = calc.items.find((x) => x.hcadNum === raw.hcadNum);
    const excl =
      calc.excludedItems.find((x) => x.hcadNum === raw.hcadNum) ||
      calc.segregatedReserves.find((x) => x.hcadNum === raw.hcadNum);
    const merged = active || excl || raw;
    const sr = smlsRankByHcad.get(raw.hcadNum);
    const br = smblRankByHcad.get(raw.hcadNum);
    return { merged, sr, br };
  });

  return `
    <div class="ch42-kit-sheet" data-seed-hcad="${escapeOrganizerHtml(assembly.seedHcadNum)}">
      <div class="ch42-header-bar">
        <div>
          <div class="scorecard-eyebrow">
            <span>City of Houston Code Chapter 42, Art. III Organizer Kit</span>
            <span>&middot; Seed Account ${escapeOrganizerHtml(assembly.seedHcadNum)}</span>
            ${calc.isHistoricDistrict ? `<span class="badge badge-accent">COH Historic District: 60% Threshold (${escapeOrganizerHtml(calc.historicDistrictName)})</span>` : `<span>&middot; Standard Urban Block Threshold: 70%</span>`}
          </div>
          <h3 class="scorecard-detail-title">
            ${escapeOrganizerHtml(assembly.hundredBlock ? `${assembly.hundredBlock} Block of ` : "")}${escapeOrganizerHtml(assembly.targetStreetKey || "Selected Blockface")}
            <span style="font-weight:400;font-size:0.9rem;color:var(--text-secondary)">
              (${assembly.seedParity !== "unknown" ? `${escapeOrganizerHtml(assembly.seedParity)}-numbered side` : "primary side"}${assembly.includeOpposingBlockface ? " + opposing blockface" : ""})
            </span>
          </h3>
        </div>
        <div class="scorecard-detail-actions no-print">
          <label class="ch42-opp-toggle-lbl" title="Sec. 42-197(b)(1): Include opposing blockface across the street right-of-way">
            <input type="checkbox" id="chk-ch42-opposing" ${assembly.includeOpposingBlockface ? "checked" : ""} />
            <span>Include Opposing Blockface</span>
          </label>
          <button type="button" class="btn-secondary" id="btn-ch42-copy-link" data-share-url="${escapeOrganizerHtml(shareUrl)}">
            Copy Blockface Link
          </button>
          <button type="button" class="btn-primary" id="btn-ch42-print-packet">
            Print Civic Club Walk-Sheet Packet
          </button>
        </div>
      </div>

      ${
        matchedOverlays.length > 0
          ? `<div class="ch42-existing-ord-banner${expiringMatches.length > 0 ? " is-expiring" : ""}">
               <strong>Existing Chapter 42 Ordinance Overlap Detected:</strong>
               ${matchedOverlays
                 .map(
                   (m) =>
                     `<span class="sc-ord-pill">${escapeOrganizerHtml(m.protectionType)} Ord. ${escapeOrganizerHtml(m.ordinance)} (${escapeOrganizerHtml(m.lifecycle.expiration_label)}${m.isRibbonGeometry ? ", frontage ribbon" : ""})</span>`
                 )
                 .join(" ")}
               ${
                 expiringMatches.length > 0
                   ? `<div style="margin-top:4px;font-size:0.76rem">One or more ordinances reach their 20-year statutory sunset by 2031. Organizers can file a new 40-year Chapter 42 application before expiration.</div>`
                   : ""
               }
             </div>`
          : ""
      }

      <div class="scorecard-metrics-grid ch42-summary-grid">
        <div class="scorecard-stat-box ${elig.passesBlock60Pct ? "is-pass" : "is-fail"}">
          <div class="sc-stat-label">1. SFR Land-Use Gate (Sec. 42-197(b)(4))</div>
          <div class="sc-stat-big">
            ${elig.sfrPct}% <span class="sc-stat-sub">${elig.passesBlock60Pct ? "ELIGIBLE (>=60%)" : "BELOW 60% GATE"}</span>
          </div>
          <div class="sc-stat-meta">
            <strong>${fmtInt(elig.sfrCount)}</strong> SFR lots of <strong>${fmtInt(elig.denominatorCount)}</strong> qualifying denominator lots
            ${elig.excludedCivicReligiousCount > 0 ? `(${fmtInt(elig.excludedCivicReligiousCount)} park/school/church/utility excluded)` : ""}<br/>
            SMLSA Area reference (>=80% SFR): <strong>${elig.passesArea80Pct ? "Meets 80%" : "Below 80%"}</strong>
          </div>
        </div>

        <div class="scorecard-stat-box">
          <div class="sc-stat-label">2. Minimum Lot Size - SMLS (Sec. 42-202)</div>
          <div class="sc-stat-big">
            ${smls.effectiveMinLotSqft !== null ? `${fmtInt(smls.effectiveMinLotSqft)} sq ft` : "N/A"}
          </div>
          <div class="sc-stat-meta">
            Threshold at <strong>>=${smls.thresholdPct}%</strong> of ${fmtInt(smls.totalAreaSqft)} sq ft across <strong>${fmtInt(smls.universeCount)}</strong> lots (incl. vacant lots)<br/>
            Protects <strong>${fmtInt(pet.townhomeSplitVulnerableLots)}</strong> lot${pet.townhomeSplitVulnerableLots === 1 ? "" : "s"} from townhome flag/shared-drive splits
          </div>
        </div>

        <div class="scorecard-stat-box">
          <div class="sc-stat-label">3. Min. Building Line - SMBL Est. (Sec. 42-173)</div>
          <div class="sc-stat-big">
            ${smbl.effectiveMinSetbackFt !== null ? `${smbl.effectiveMinSetbackFt.toFixed(1)} ft` : "N/A"}
          </div>
          <div class="sc-stat-meta">
            Threshold at <strong>>=${smbl.thresholdPct}%</strong> of ${smbl.totalBuildingLineFt.toFixed(1)} combined ft across <strong>${fmtInt(smbl.constructedLotCount)}</strong> constructed lots
            (${fmtInt(smbl.excludedVacantOrDemolishedCount)} vacant/demolished excluded${smbl.missingFootprintConstructedCount > 0 ? `, ${fmtInt(smbl.missingFootprintConstructedCount)} awaiting manual setback` : ""})
          </div>
        </div>

        <div class="scorecard-stat-box ${pet.meetsDirectToCouncilFastTrack ? "is-pass" : ""}">
          <div class="sc-stat-label">4. Direct-to-Council Fast Track (Sec. 42-200(e))</div>
          <div class="sc-stat-big">
            ${fmtInt(pet.directToCouncilArea51PctSqft)} sq ft <span class="sc-stat-sub">51% land area</span>
          </div>
          <div class="sc-stat-meta">
            Signed tracker: <strong>${fmtInt(pet.signedLandAreaSqft)} sq ft (${pet.signedLandAreaPct}%)</strong> across ${fmtInt(pet.signedOwnerCount)} owner${pet.signedOwnerCount === 1 ? "" : "s"}<br/>
            Block filing min: <strong>1 owner</strong> &middot; Ownership: ${fmtInt(pet.ownershipBreakdown.individualOwners)} ind., ${fmtInt(pet.ownershipBreakdown.familyTrustOwners)} trust, ${fmtInt(pet.ownershipBreakdown.corporateOwners)} LLC/corp
          </div>
        </div>
      </div>

      <div class="ch42-statutory-caveat">
        <strong>Statutory &amp; Survey Notes:</strong>
        (1) Under <strong>COH Code Sec. 42-173(b)</strong>, uninhabitable front porches and porte-cocheres are excluded when measuring building lines (measure to the enclosed facade wall), and <strong>Sec. 42-171(a)(4)</strong> requires submitting a survey/plat on at least 1 blockface lot alongside aerial/field measurements for remaining lots. Override any aerial roof estimate below before printing.
        (2) Before collecting signatures, schedule the mandatory Pre-Submittal Meeting (<strong>Sec. 42-198(a)(2)</strong>) with COH Planning &amp; Development at <strong>${escapeOrganizerHtml(COH_CHAPTER_42_RESOURCES.planningIntakePhone)}</strong>.
      </div>

      <div class="ch42-official-links no-print">
        <a href="${COH_CHAPTER_42_RESOURCES.smlsBlockAppPdfUrl}" target="_blank" rel="noopener noreferrer" class="btn-secondary btn-xs">SMLS Block Application PDF &#8599;</a>
        <a href="${COH_CHAPTER_42_RESOURCES.smblBlockAppPdfUrl}" target="_blank" rel="noopener noreferrer" class="btn-secondary btn-xs">SMBL Block Application PDF &#8599;</a>
        <a href="${COH_CHAPTER_42_RESOURCES.smlsAreaAppPdfUrl}" target="_blank" rel="noopener noreferrer" class="btn-secondary btn-xs">SMLS Area Application PDF &#8599;</a>
        <a href="${COH_CHAPTER_42_RESOURCES.brochurePdfUrl}" target="_blank" rel="noopener noreferrer" class="btn-secondary btn-xs">COH Homeowner Brochure &#8599;</a>
        <a href="${COH_CHAPTER_42_RESOURCES.smlsOrdPdfUrl}" target="_blank" rel="noopener noreferrer" class="btn-secondary btn-xs">SMLS Ordinance Text &#8599;</a>
        <a href="${COH_CHAPTER_42_RESOURCES.smblOrdPdfUrl}" target="_blank" rel="noopener noreferrer" class="btn-secondary btn-xs">SMBL Ordinance Text &#8599;</a>
        <a href="${COH_CHAPTER_42_RESOURCES.hubUrl}" target="_blank" rel="noopener noreferrer" class="btn-secondary btn-xs">COH Ch. 42 Portal &#8599;</a>
      </div>

      <div class="ch42-table-wrap">
        <table class="ch42-lot-table">
          <thead>
            <tr>
              <th class="no-print">Inc.</th>
              <th>Address &amp; HCAD Account</th>
              <th>Owner &amp; Legal Description</th>
              <th>Ch. 42 Land Use</th>
              <th style="text-align:right">Lot Area &amp; SMLS Cum.%</th>
              <th style="text-align:right">Front Setback &amp; SMBL Cum.%</th>
              <th>Petition Sign-Off / Walk Notes</th>
            </tr>
          </thead>
          <tbody>
            ${allDisplayRows
              .map(({ merged, sr, br }) => {
                const isCut = sr?.isCutoffLot || br?.isCutoffLot;
                return `
                  <tr class="ch42-lot-row${merged.included ? "" : " is-excluded"}${isCut ? " is-cutoff-lot" : ""}" data-hcad="${escapeOrganizerHtml(merged.hcadNum)}">
                    <td class="no-print" style="text-align:center">
                      <input type="checkbox" class="ch42-inc-chk" data-hcad="${escapeOrganizerHtml(merged.hcadNum)}" ${merged.included ? "checked" : ""} aria-label="Include ${escapeOrganizerHtml(merged.address || merged.hcadNum)} in blockface" />
                    </td>
                    <td>
                      <div style="font-weight:600">${escapeOrganizerHtml(merged.address || "Unnumbered Parcel")}</div>
                      <div style="font-size:0.72rem;color:var(--text-secondary)">
                        <code>${escapeOrganizerHtml(merged.hcadNum)}</code>
                        ${merged.yearBuilt ? ` &middot; Built ${merged.yearBuilt}` : merged.isVacant ? " &middot; Vacant" : ""}
                        ${merged.frontageRole !== "interior" ? ` &middot; <span class="ch42-role-tag">${escapeOrganizerHtml(merged.frontageRole.replace(/_/g, " "))}</span>` : ""}
                      </div>
                    </td>
                    <td>
                      <div style="font-size:0.78rem">${escapeOrganizerHtml(merged.owner || "Unknown Owner")}</div>
                      <div style="font-size:0.7rem;color:var(--text-secondary)">${escapeOrganizerHtml(merged.legalLines || merged.subdivision || "")}</div>
                      ${
                        merged.warnings?.length
                          ? `<div class="ch42-lot-warn">${merged.warnings.map((w) => escapeOrganizerHtml(w)).join(" &bull; ")}</div>`
                          : ""
                      }
                    </td>
                    <td style="font-size:0.75rem">
                      <div>${escapeOrganizerHtml(merged.classification?.landUseLabel || "")}</div>
                      ${
                        merged.isVacant
                          ? `<label class="no-print ch42-vacant-toggle">
                               <input type="checkbox" class="ch42-vac-sfr-chk" data-hcad="${escapeOrganizerHtml(merged.hcadNum)}" ${merged.vacantWasSfrPast5Years ? "checked" : ""} />
                               <span>SFR within past 5 yrs</span>
                             </label>`
                          : ""
                      }
                    </td>
                    <td style="text-align:right;font-variant-numeric:tabular-nums">
                      <div><strong>${fmtInt(merged.lotSqftInt)}</strong> sq ft</div>
                      ${
                        sr
                          ? `<div style="font-size:0.71rem;color:var(--text-secondary)">#${sr.rank} &middot; Cum. ${sr.cumulativePct}%${sr.isCutoffLot ? ` <span class="ch42-cut-badge">SMLS CUT</span>` : ""}</div>`
                          : `<div style="font-size:0.7rem;color:var(--text-muted)">Excluded</div>`
                      }
                    </td>
                    <td style="text-align:right;font-variant-numeric:tabular-nums">
                      ${
                        merged.isVacant || merged.isSuppressedVacant
                          ? `<span style="font-size:0.73rem;color:var(--text-muted)">Vacant (Excl.)</span>`
                          : `<div class="ch42-sb-cell">
                               <input type="number" step="0.5" min="1" max="300"
                                 class="ch42-sb-input no-print"
                                 data-hcad="${escapeOrganizerHtml(merged.hcadNum)}"
                                 placeholder="${merged.estimatedSetbackFt != null ? merged.estimatedSetbackFt.toFixed(1) : "--"}"
                                 value="${merged.manualSetbackFt != null ? merged.manualSetbackFt : merged.effectiveSetbackFt != null ? merged.effectiveSetbackFt : ""}"
                                 aria-label="Front setback feet for ${escapeOrganizerHtml(merged.address || merged.hcadNum)}" />
                               <span class="print-only-inline">${merged.effectiveSetbackFt != null ? `${merged.effectiveSetbackFt.toFixed(1)} ft` : "Field check"}</span>
                               <span class="no-print" style="font-size:0.72rem">ft</span>
                             </div>
                             ${
                               br
                                 ? `<div style="font-size:0.71rem;color:var(--text-secondary)">#${br.rank} &middot; Cum. ${br.cumulativePct}%${br.isCutoffLot ? ` <span class="ch42-cut-badge">SMBL CUT</span>` : ""}</div>`
                                 : `<div style="font-size:0.7rem;color:var(--text-muted)">Needs setback</div>`
                             }`
                      }
                    </td>
                    <td>
                      <label class="no-print ch42-sign-lbl">
                        <input type="checkbox" class="ch42-sign-chk" data-hcad="${escapeOrganizerHtml(merged.hcadNum)}" ${merged.supportSigned ? "checked" : ""} />
                        <span>Owner Committed</span>
                      </label>
                      <div class="ch42-print-sig-line">${merged.supportSigned ? "[X] Committed / Signed" : "[ ] Support   [ ] Undecided   Sig: ________________"}</div>
                    </td>
                  </tr>
                `;
              })
              .join("")}
          </tbody>
        </table>
      </div>

      <div class="scorecard-print-footer">
        Prepared with The Houston Building Atlas Chapter 42 Civic Club Kit &middot; Estimates derived from HCAD Parcels (Shape.STArea) and building roof footprints; official applications are verified by City of Houston Planning &amp; Development (${escapeOrganizerHtml(COH_CHAPTER_42_RESOURCES.planningIntakePhone)}, ${escapeOrganizerHtml(COH_CHAPTER_42_RESOURCES.hubUrl)}). Rescission requires &gt;=67% area signatures (${fmtInt(pet.rescissionArea67PctSqft)} sq ft).
        ${shareUrl ? `<br/>Blockface Worksheet URL: <span>${escapeOrganizerHtml(shareUrl)}</span>` : ""}
      </div>
    </div>
  `;
}
