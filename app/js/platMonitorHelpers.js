/**
 * Pure, zero-DOM helper functions for the Biweekly Planning Commission Plat & Variance Monitor.
 *
 * Enforces behavioural parity with pipeline/atlas_pipeline/fetch_plat_and_watchlist.py across:
 *   1. Floored Excel serial date conversion (eliminating +1 day drift on 2:30 PM .60417 serials).
 *   2. Pass 1 rule-based screening (ensuring blank-prevSub items with variances, hearings, DPVs,
 *      reconsiderations, pre-1976 structures, active permits, or designations never screen out).
 *   3. Multi-building and multi-HCAD parcel construction-year aggregation (minimum valid nonzero
 *      year_built >= 1836 with curated_overrides.json precedence).
 *   4. Per-cycle composite occurrence keys (<cycleIso>::<appNo>) decoupled from strict
 *      section-preserving cross-cycle continuity keys.
 *   5. Human-contribution dossier detection guarding Tier 3 downgrades and cleanups.
 */

export const THREAT_CATEGORIES = [
  "Significant Building Threatened",
  "Neighborhood / District Context Impact",
  "Lot Split / Subdividing Historic Lot",
  "Setback / Walkable Realm Variance",
  "Institutional / Cultural Site",
  "No Historic Impact",
];

export const CARRY_FORWARD_REVIEW_FIELDS = [
  "hcad",
  "address",
  "yearBuilt",
  "designation",
  "tier",
  "threatCategory",
  "why",
  "sources",
  "followup",
];

export const RETRACTED_BASELINE_PLAT_SEEDS = {
  "DPV-26073533": ["2603 MILAM ST 15", "2603 MILAM"],
  "2026-1585": ["1226 W CLAY ST", "MONTROSE RESIDENCES"],
};

export const HCAD_PUBLIC_QUERY_OUT_FIELDS =
  "HCAD_NUM,owner,subdivision,address,city,zip,state_class,appr_val,mkt_val,impr_val,land_val,legal_lines";

export const HCAD_RESIDENTIAL_GRADE_OUT_FIELDS = "HCAD_NUM,year_impr,grade";

export const OLDER_RING_SUPER_NEIGHBORHOODS = new Set([
  "ACRES HOME",
  "CENTRAL NORTHWEST",
  "DENVER HARBOR / PORT HOUSTON",
  "DOWNTOWN",
  "EAST END",
  "EAST HOUSTON",
  "EAST LITTLE YORK / HOMESTEAD",
  "EASTEX - JENSEN AREA",
  "FOURTH WARD",
  "GREATER EASTWOOD",
  "GREATER FIFTH WARD",
  "GREATER HEIGHTS",
  "GREATER OST / SOUTH UNION",
  "GREATER THIRD WARD",
  "GREATER UPTOWN",
  "GULFGATE RIVERVIEW / PINE VALLEY",
  "INDEPENDENCE HEIGHTS",
  "KASHMERE GARDENS",
  "LAWNDALE / WAYSIDE",
  "MACGREGOR",
  "MAGNOLIA PARK",
  "MEMORIAL",
  "MIDTOWN",
  "MUSEUM PARK",
  "NEAR NORTHSIDE",
  "NEARTOWN - MONTROSE",
  "NORTHSIDE/NORTHLINE",
  "NORTHSIDE VILLAGE",
  "PARK PLACE",
  "PECAN PARK",
  "SECOND WARD",
  "SETTEGAST",
  "SOUTH ACRES / CRESTMONT PARK",
  "SOUTH PARK",
  "SPRING BRANCH CENTRAL",
  "SPRING BRANCH EAST",
  "SPRING BRANCH WEST",
  "SUNNYSIDE",
  "TRINITY / HOUSTON GARDENS",
  "UNIVERSITY PLACE",
  "WASHINGTON AVENUE COALITION / MEMORIAL PARK",
]);

const UTILITY_RE =
  /lift station|water treatment|water plant|wastewater|ground water|utility|detention only/i;
const INST_RE =
  /\b(church|chapel|school|temple|mosque|synagogue|theat(?:er|re)|lodge|hospital|clinic|cemetery)\b/i;

const GENERIC_STANDALONE_PLAT_NAMES = new Set([
  "RESERVE",
  "RESERVE A",
  "RESERVE B",
  "COMMERCIAL RESERVE",
  "UNRESTRICTED RESERVE",
  "TRACT",
  "TRACT 1",
  "TRACT A",
  "REPLAT",
  "PARTIAL REPLAT",
  "AMENDING PLAT",
  "DEVELOPMENT PLAT",
  "ESTATES",
  "PARK",
  "PLACE",
  "GARDENS",
  "ACRES",
  "VILLAGE",
  "HEIGHTS",
  "SQUARE",
  "COURT",
  "MANOR",
  "TERRACE",
  "OAKS",
  "WOODS",
  "HILLS",
  "VILLAS",
  "CENTER",
  "PLAZA",
  "CROSSING",
  "COMMONS",
  "GROVE",
]);

const MONTH_NAME_MAP = {
  january: 1,
  jan: 1,
  february: 2,
  feb: 2,
  march: 3,
  mar: 3,
  april: 4,
  apr: 4,
  may: 5,
  june: 6,
  jun: 6,
  july: 7,
  jul: 7,
  august: 8,
  aug: 8,
  september: 9,
  sep: 9,
  sept: 9,
  october: 10,
  oct: 10,
  november: 11,
  nov: 11,
  december: 12,
  dec: 12,
};

function parseFiniteNum(val) {
  if (val == null || val === "") return null;
  const cleaned = String(val).replace(/,/g, "").trim();
  if (!cleaned) return null;
  const n = Number(cleaned);
  return Number.isFinite(n) ? n : null;
}

/**
 * Convert an Excel 1900-system serial number or date string into an ISO YYYY-MM-DD calendar date.
 *
 * Why Math.floor(n): City of Houston Planning Commission spreadsheets encode 2:30 PM meeting times
 * in the fractional portion of the Excel serial (e.g. 46310.6041666667 = Oct 15, 2026 at 14:30).
 * Rounding shifts afternoon serials (>= 0.5) forward to Friday (+1 day). Flooring the day integer
 * relative to Excel epoch 1899-12-30 UTC matches Python int(math.floor(n)) across all times of day.
 */
export function excelSerialToIso(val) {
  if (val == null || val === "" || typeof val === "boolean") return "";
  const s = String(val).trim();
  if (!s) return "";

  if (/^[+-]?\d+(?:\.\d+)?$/.test(s)) {
    const n = Number(s);
    if (Number.isFinite(n) && n > 20000 && n < 80000) {
      const dayInt = Math.floor(n);
      const baseUtcMs = Date.UTC(1899, 11, 30);
      const d = new Date(baseUtcMs + dayInt * 86400000);
      return d.toISOString().slice(0, 10);
    }
  }

  const isoMatch = s.match(/^(\d{4})-(\d{2})-(\d{2})(?:$|T|\s)/);
  if (isoMatch) {
    const y = Number(isoMatch[1]);
    const m = Number(isoMatch[2]);
    const day = Number(isoMatch[3]);
    if (m >= 1 && m <= 12 && day >= 1 && day <= 31) {
      const check = new Date(Date.UTC(y, m - 1, day));
      if (check.getUTCFullYear() === y && check.getUTCMonth() === m - 1 && check.getUTCDate() === day) {
        return check.toISOString().slice(0, 10);
      }
    }
    return "";
  }

  const usMatch = s.match(/^(\d{1,2})[/-](\d{1,2})[/-](\d{4})(?:$|\s)/);
  if (usMatch) {
    const m = Number(usMatch[1]);
    const day = Number(usMatch[2]);
    const y = Number(usMatch[3]);
    if (m >= 1 && m <= 12 && day >= 1 && day <= 31) {
      const check = new Date(Date.UTC(y, m - 1, day));
      if (check.getUTCFullYear() === y && check.getUTCMonth() === m - 1 && check.getUTCDate() === day) {
        return check.toISOString().slice(0, 10);
      }
    }
    return "";
  }

  const humanMatch = s.match(
    /(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+)?([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})/i
  );
  if (humanMatch) {
    const m = MONTH_NAME_MAP[humanMatch[1].toLowerCase()];
    const day = Number(humanMatch[2]);
    const y = Number(humanMatch[3]);
    if (m && day >= 1 && day <= 31) {
      const check = new Date(Date.UTC(y, m - 1, day));
      if (check.getUTCFullYear() === y && check.getUTCMonth() === m - 1 && check.getUTCDate() === day) {
        return check.toISOString().slice(0, 10);
      }
    }
  }

  return "";
}

export function excelSerialToDate(val) {
  const iso = excelSerialToIso(val);
  if (!iso) return null;
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d));
}

export function formatShortUsDate(val) {
  const iso = excelSerialToIso(val);
  if (!iso) return "";
  const [y, m, d] = iso.split("-");
  return `${m}/${d}/${y}`;
}

export function formatLongUsDate(val, shortMonth = false) {
  const dtObj = excelSerialToDate(val);
  if (!dtObj) return "";
  return dtObj.toLocaleDateString("en-US", {
    timeZone: "UTC",
    month: shortMonth ? "short" : "long",
    day: "numeric",
    year: "numeric",
  });
}

export function parseTypeCode(typeStr = "") {
  const m = /\(([A-Z0-9]+)\)\s*$/.exec(String(typeStr || "").trim());
  return m ? m[1] : "";
}

/**
 * Parse a comma-, semicolon-, or whitespace-delimited string of HCAD parcel numbers.
 * Left-pads 10-13 digit HCAD integers to 13 digits and separates >13 digit non-HCAD IDs
 * or scientific-notation strings into warnings without corrupting valid accounts.
 */
export function parseHcadAccountList(raw = "") {
  const text = String(raw || "").trim();
  if (!text) return { hcadIds: [], nonHcadIds: [], warnings: [] };

  const tokens = text
    .split(/[,;\s|]+/)
    .map((t) => t.replace(/^#/, "").trim())
    .filter(Boolean);

  const seen = new Set();
  const hcadIds = [];
  const nonHcadIds = [];
  const warnings = [];

  for (const rawTok of tokens) {
    const tok = rawTok.split("#")[0].trim();
    if (!tok) continue;
    if (/^[+-]?\d+(?:\.\d+)?E[+-]?\d+$/i.test(tok)) {
      nonHcadIds.push(tok);
      warnings.push(`Scientific notation '${tok}' lost 13-digit HCAD precision.`);
      continue;
    }
    const digitsOnly = tok.replace(/-/g, "");
    if (/^\d+$/.test(digitsOnly)) {
      if (digitsOnly.length >= 10 && digitsOnly.length <= 13 && !/^0+$/.test(digitsOnly)) {
        const padded = digitsOnly.padStart(13, "0");
        if (!seen.has(padded)) {
          seen.add(padded);
          hcadIds.push(padded);
        }
      } else if (digitsOnly.length > 13) {
        nonHcadIds.push(digitsOnly);
        warnings.push(
          `Parcel ID '${digitsOnly}' has ${digitsOnly.length} digits (likely Fort Bend or Montgomery County rather than 13-digit Harris County HCAD).`
        );
      }
    }
  }

  return { hcadIds, nonHcadIds, warnings };
}

/**
 * Resolve oldest nonzero standing construction year (>= 1836) and suggested preservation tier
 * across one or more HCAD parcels, including multi-building commercial parcels (such as
 * Amerimex Motors with mixed c. 1940 and 2013 buildings plus vacant 0-year sub-parcels).
 *
 * Why >= 1836 filter: HCAD assigns year_built = 0 to vacant land, parking lots, and unimproved
 * reserves; unfiltered Math.min() would pick 0 instead of the oldest standing historic structure.
 */
export function resolveMultiParcelBuildingTier(
  parcels = [],
  {
    flags = [],
    superN = "",
    curatedOverrides = null,
    confirmedVacant = false,
    overrideSuggestedTier = "",
  } = {}
) {
  const overridesMap =
    curatedOverrides && typeof curatedOverrides === "object"
      ? curatedOverrides.overrides || curatedOverrides
      : null;

  const validAllYears = [];
  const parcelSummaries = [];
  const designations = [];

  const collectValidYear = (val) => {
    const n = Number(val);
    if (Number.isFinite(n) && n >= 1836 && n <= 2035) {
      return Math.trunc(n);
    }
    return null;
  };

  for (const rawP of Array.isArray(parcels) ? parcels : []) {
    if (!rawP || typeof rawP !== "object") continue;
    const rawHcad = String(rawP.hcad || rawP.hcad_num || "").trim();
    const parsedHcadIds = parseHcadAccountList(rawHcad).hcadIds;
    const hcad = parsedHcadIds[0] || rawHcad.split("#")[0].trim();
    const ov =
      hcad && overridesMap && overridesMap[hcad] && !overridesMap[hcad].suppress_only
        ? overridesMap[hcad]
        : null;
    const isDemolishedOverride = Boolean(hcad && overridesMap && overridesMap[hcad]?.suppress_only);

    const hd = String(ov?.historic_district || rawP.historic_district || "").trim();
    const lm = String(ov?.landmark_name || rawP.landmark_name || "").trim();
    const gb = String(ov?.good_brick_summary || rawP.good_brick_summary || "").trim();
    const rawDesigs = Array.isArray(rawP.designations) ? rawP.designations.filter(Boolean) : [];
    const hasDesignation = Boolean(hd || lm || gb || rawDesigs.length > 0);

    const candidateYears = [];
    const ovYr = isDemolishedOverride
      ? null
      : collectValidYear(rawP.overrideYearBuilt ?? ov?.year_built);
    if (ovYr !== null) {
      // Curated override takes precedence for the parcel's primary historic date
      candidateYears.push(ovYr);
    } else if (!isDemolishedOverride && !(confirmedVacant && !hasDesignation)) {
      const many = Array.isArray(rawP.allYears)
        ? rawP.allYears
        : Array.isArray(rawP.all_years)
        ? rawP.all_years
        : [];
      for (const y of many) {
        const cy = collectValidYear(y);
        if (cy !== null) candidateYears.push(cy);
      }
      for (const single of [rawP.yearBuilt, rawP.year_built, rawP.earliestYearBuilt]) {
        const cy = collectValidYear(single);
        if (cy !== null) candidateYears.push(cy);
      }
    }

    const dedupYears = Array.from(new Set(candidateYears)).sort((a, b) => a - b);
    const parcelOldest = dedupYears.length > 0 ? dedupYears[0] : 0;
    for (const y of dedupYears) validAllYears.push(y);

    const address = String(ov?.address || rawP.address || "")
      .replace(/\s+/g, " ")
      .trim();
    const buildingName = String(
      ov?.landmark_name || ov?.building_name || rawP.landmark_name || rawP.building_name || ""
    ).trim();

    if (hd) designations.push(`City historic district (${hd})`);
    if (lm) designations.push("City Landmark");
    if (gb) designations.push("Good Brick Award");
    for (const d of rawDesigs) {
      designations.push(String(d));
    }

    parcelSummaries.push({
      hcad,
      address,
      buildingName,
      oldestYearBuilt: parcelOldest,
      allYearsBuilt: dedupYears,
      usedCuratedOverride: ovYr !== null,
      isVacant: Boolean(rawP.isVacant || isDemolishedOverride || (confirmedVacant && dedupYears.length === 0)),
    });
  }

  const sortedAllYears = Array.from(new Set(validAllYears)).sort((a, b) => a - b);
  const oldestYearBuilt = sortedAllYears.length > 0 ? sortedAllYears[0] : 0;
  const newestYearBuilt = sortedAllYears.length > 0 ? sortedAllYears[sortedAllYears.length - 1] : 0;
  const uniqueDesignations = Array.from(new Set(designations));
  const flagSet = new Set(Array.isArray(flags) ? flags : []);
  const normSn = String(superN || "").trim().toUpperCase();

  const allParcelsConfirmedVacant =
    confirmedVacant ||
    (parcelSummaries.length > 0 && parcelSummaries.every((p) => p.isVacant));
  const primaryAddress =
    parcelSummaries.find((p) => p.address && !p.isVacant)?.address ||
    parcelSummaries.find((p) => p.address)?.address ||
    "";

  let suggestedTier = "";
  if (overrideSuggestedTier) {
    suggestedTier = String(overrideSuggestedTier).trim();
  } else if (allParcelsConfirmedVacant && uniqueDesignations.length === 0 && oldestYearBuilt === 0) {
    suggestedTier = "3";
  } else if (uniqueDesignations.length > 0) {
    suggestedTier = "1";
  } else if (flagSet.has("dpv")) {
    suggestedTier =
      (oldestYearBuilt >= 1836 && oldestYearBuilt <= 1976) ||
      OLDER_RING_SUPER_NEIGHBORHOODS.has(normSn)
        ? "1"
        : "2";
  } else if (
    oldestYearBuilt >= 1836 &&
    oldestYearBuilt <= 1965 &&
    (flagSet.has("hearing") ||
      flagSet.has("variance") ||
      flagSet.has("split") ||
      flagSet.has("use") ||
      oldestYearBuilt <= 1935)
  ) {
    suggestedTier =
      flagSet.has("hearing") || flagSet.has("variance") || oldestYearBuilt <= 1935 ? "1" : "2";
  } else if (oldestYearBuilt >= 1836 && oldestYearBuilt <= 1976) {
    suggestedTier = "2";
  }

  return {
    oldestYearBuilt,
    earliestYearBuilt: oldestYearBuilt,
    newestYearBuilt,
    allYearsBuilt: sortedAllYears,
    allParcelYears: sortedAllYears,
    primaryAddress,
    parcels: parcelSummaries,
    designations: uniqueDesignations,
    suggestedTier,
    confirmedVacant: allParcelsConfirmedVacant,
  };
}

/**
 * Compute an area-weighted geometric centroid for a GeoJSON Point, Polygon, or MultiPolygon,
 * falling back to bounding-box midpoint so dense curved subdivision arcs never skew pin placement.
 */
export function computeGeoJsonCentroid(geometry) {
  if (!geometry || typeof geometry !== "object") return null;
  if (geometry.type === "Point" && Array.isArray(geometry.coordinates)) {
    const [lng, lat] = geometry.coordinates;
    return Number.isFinite(Number(lng)) && Number.isFinite(Number(lat))
      ? [Number(lng), Number(lat)]
      : null;
  }
  const outerRings = [];
  if (geometry.type === "Polygon" && Array.isArray(geometry.coordinates?.[0])) {
    outerRings.push(geometry.coordinates[0]);
  } else if (geometry.type === "MultiPolygon" && Array.isArray(geometry.coordinates)) {
    for (const poly of geometry.coordinates) {
      if (Array.isArray(poly?.[0])) outerRings.push(poly[0]);
    }
  }
  if (outerRings.length === 0) return null;

  let bestAreaAbs = -1;
  let bestCentroid = null;
  let minLng = Infinity;
  let maxLng = -Infinity;
  let minLat = Infinity;
  let maxLat = -Infinity;

  for (const ring of outerRings) {
    if (!Array.isArray(ring) || ring.length < 3) continue;
    let signedArea2 = 0;
    let cxAcc = 0;
    let cyAcc = 0;
    let validPts = 0;
    for (let i = 0; i < ring.length; i += 1) {
      const x0 = Number(ring[i]?.[0]);
      const y0 = Number(ring[i]?.[1]);
      if (Number.isFinite(x0) && Number.isFinite(y0)) {
        validPts += 1;
        if (x0 < minLng) minLng = x0;
        if (x0 > maxLng) maxLng = x0;
        if (y0 < minLat) minLat = y0;
        if (y0 > maxLat) maxLat = y0;
      }
      const next = ring[(i + 1) % ring.length];
      const x1 = Number(next?.[0]);
      const y1 = Number(next?.[1]);
      if (!Number.isFinite(x0) || !Number.isFinite(y0) || !Number.isFinite(x1) || !Number.isFinite(y1)) {
        continue;
      }
      const cross = x0 * y1 - x1 * y0;
      signedArea2 += cross;
      cxAcc += (x0 + x1) * cross;
      cyAcc += (y0 + y1) * cross;
    }
    const areaAbs = Math.abs(signedArea2) * 0.5;
    if (validPts >= 3 && areaAbs > 1e-12 && areaAbs > bestAreaAbs) {
      bestAreaAbs = areaAbs;
      bestCentroid = [cxAcc / (3 * signedArea2), cyAcc / (3 * signedArea2)];
    }
  }

  if (bestCentroid && Number.isFinite(bestCentroid[0]) && Number.isFinite(bestCentroid[1])) {
    return [Number(bestCentroid[0].toFixed(6)), Number(bestCentroid[1].toFixed(6))];
  }
  if (Number.isFinite(minLng) && Number.isFinite(maxLng)) {
    return [Number(((minLng + maxLng) / 2).toFixed(6)), Number(((minLat + maxLat) / 2).toFixed(6))];
  }
  return null;
}

/**
 * Resolve building years, vacancy status, owner, situs address, legal description, and centroid
 * for one or more 13-digit HCAD parcels across offline Atlas indices and live HCAD ArcGIS services (F2-03, DF-3).
 *
 * Key invariants:
 *   1. Exact 13-digit HCAD structural records in curatedOverrides / localHcadMap / address_shards are
 *      inspected BEFORE evaluating impr_val === 0 so tax-exempt X* institutions (e.g. Houston Chinese
 *      Church 0451910000150 [1981] & 0451910000296 [1983]) keep their verified building dates.
 *   2. Non-exempt parcels with impr_val === 0 and zero local structures resolve as explicit vacant
 *      parcels (found: true, isVacant: true) rather than false "account not found" warnings.
 *   3. Double spaces in HCAD directional streets ("2915 W  BELLFORT AVE") and "0 ..." unassigned situs
 *      numbers are normalized cleanly.
 */
export function resolveParcelsMetadataFromSources(
  hcadIds = [],
  {
    shardRecordsByHcad = null,
    localHcadMap = null,
    curatedOverrides = null,
    arcgisParcelsByHcad = null,
    arcgisMap = null,
    gradeYearsByHcad = null,
    arcgisWarnings = [],
  } = {}
) {
  const effectiveArcgisStore = arcgisParcelsByHcad || arcgisMap;
  const rawInput = Array.isArray(hcadIds) ? hcadIds.join(",") : String(hcadIds || "");
  const parsed = parseHcadAccountList(rawInput);
  const warnings = [...parsed.warnings, ...(Array.isArray(arcgisWarnings) ? arcgisWarnings : [])];
  const overridesMap =
    curatedOverrides && typeof curatedOverrides === "object"
      ? curatedOverrides.overrides || curatedOverrides
      : {};

  const getFromMapOrObj = (store, key) => {
    if (!store) return null;
    if (store instanceof Map) return store.get(key) ?? null;
    return Object.prototype.hasOwnProperty.call(store, key) ? store[key] : null;
  };

  const validYr = (val) => {
    const n = Number(val);
    return Number.isFinite(n) && n >= 1836 && n <= 2035 ? Math.trunc(n) : null;
  };

  const cleanSitus = (addr) => {
    const norm = String(addr || "")
      .replace(/\s+/g, " ")
      .trim();
    if (!norm || /^0(?:\s+|$)/.test(norm)) return "";
    return norm;
  };

  const parcels = [];
  const unfoundHcads = [];
  const centroidLngs = [];
  const centroidLats = [];

  for (const hcad of parsed.hcadIds) {
    const ov = overridesMap[hcad] || null;
    const isDemolishedOv = Boolean(ov?.suppress_only);
    const subBuildingOvs = Object.entries(overridesMap)
      .filter(([k, v]) => k.startsWith(`${hcad}#`) && v && !v.suppress_only)
      .map(([, v]) => v);
    const loc = getFromMapOrObj(localHcadMap, hcad);
    const shardRecsRaw = getFromMapOrObj(shardRecordsByHcad, hcad);
    const shardRecs = Array.isArray(shardRecsRaw) ? shardRecsRaw : shardRecsRaw ? [shardRecsRaw] : [];
    const arc = getFromMapOrObj(effectiveArcgisStore, hcad);
    const gradeRaw = getFromMapOrObj(gradeYearsByHcad, hcad);
    const gradeList = Array.isArray(gradeRaw) ? gradeRaw : gradeRaw != null ? [gradeRaw] : [];

    const foundAnywhere = Boolean(
      ov || subBuildingOvs.length > 0 || loc || shardRecs.length > 0 || arc || gradeList.length > 0
    );
    if (!foundAnywhere) {
      unfoundHcads.push(hcad);
      continue;
    }

    const localYears = [];
    const ovPrimaryYr = !isDemolishedOv ? validYr(ov?.year_built) : null;
    if (ovPrimaryYr !== null) localYears.push(ovPrimaryYr);
    if (!isDemolishedOv) {
      for (const subOv of subBuildingOvs) {
        const sy = validYr(subOv?.year_built);
        if (sy !== null) localYears.push(sy);
      }
      if (loc) {
        for (const y of Array.isArray(loc.allYears) ? loc.allYears : []) {
          const ly = validYr(y);
          if (ly !== null) localYears.push(ly);
        }
        for (const y of [loc.yearBuilt, loc.year_built, loc.earliestYearBuilt]) {
          const ly = validYr(y);
          if (ly !== null) localYears.push(ly);
        }
      }
      for (const sr of shardRecs) {
        const sy = Array.isArray(sr) ? validYr(sr[3]) : validYr(sr?.yearBuilt ?? sr?.year_built);
        if (sy !== null) localYears.push(sy);
      }
    }

    const gradeYears = [];
    for (const g of gradeList) {
      const gy = typeof g === "object" ? validYr(g?.year_impr ?? g?.yearBuilt) : validYr(g);
      if (gy !== null) gradeYears.push(gy);
    }

    const stateClass = String(arc?.state_class ?? arc?.stateClass ?? loc?.stateClass ?? "")
      .trim()
      .toUpperCase();
    const rawImprVal = arc?.impr_val ?? arc?.imprVal ?? null;
    const imprVal =
      rawImprVal !== null && rawImprVal !== undefined && String(rawImprVal).trim() !== ""
        ? Number(rawImprVal)
        : null;
    const isTaxExempt = stateClass.startsWith("X");
    const isUnimprovedNonExempt =
      imprVal !== null && Number.isFinite(imprVal) && imprVal === 0 && !isTaxExempt;

    let allYears = [];
    if (isDemolishedOv) {
      allYears = [];
    } else if (ovPrimaryYr !== null) {
      allYears = Array.from(new Set([ovPrimaryYr, ...localYears])).sort((a, b) => a - b);
    } else if (localYears.length > 0) {
      const merged = isUnimprovedNonExempt ? localYears : [...localYears, ...gradeYears];
      allYears = Array.from(new Set(merged)).sort((a, b) => a - b);
    } else if (!isUnimprovedNonExempt) {
      const arcYr = validYr(arc?.yearBuilt ?? arc?.year_built ?? arc?.year_impr);
      const fallbackYrs = [...gradeYears, ...(arcYr !== null ? [arcYr] : [])];
      allYears = Array.from(new Set(fallbackYrs)).sort((a, b) => a - b);
    }

    let shardAddr = "";
    let shardCoord = null;
    for (const sr of shardRecs) {
      if (Array.isArray(sr)) {
        const sLng = Number(sr[1]);
        const sLat = Number(sr[2]);
        if (!shardCoord && Number.isFinite(sLng) && Number.isFinite(sLat)) {
          shardCoord = [sLng, sLat];
        }
      } else if (sr && typeof sr === "object") {
        const candAddr = cleanSitus(
          sr.address || (sr.houseNum && sr.street ? `${sr.houseNum} ${sr.street}` : "")
        );
        if (!shardAddr && candAddr) shardAddr = candAddr;
        const sLng = Number(sr.lng);
        const sLat = Number(sr.lat);
        if (!shardCoord && Number.isFinite(sLng) && Number.isFinite(sLat)) {
          shardCoord = [sLng, sLat];
        }
      }
    }

    const address =
      cleanSitus(ov?.address) ||
      cleanSitus(loc?.address) ||
      shardAddr ||
      cleanSitus(arc?.address) ||
      "";
    const owner = String(ov?.owner || arc?.owner || loc?.owner || "")
      .replace(/\s+/g, " ")
      .trim();
    const subdivision = String(arc?.subdivision || loc?.subdivision || "")
      .replace(/\s+/g, " ")
      .trim();
    const legalDescription = String(arc?.legal_lines || arc?.legalDescription || loc?.legalDescription || "")
      .replace(/\s+/g, " ")
      .trim();

    let lng = null;
    let lat = null;
    if (Number.isFinite(Number(loc?.lng)) && Number.isFinite(Number(loc?.lat))) {
      lng = Number(loc.lng);
      lat = Number(loc.lat);
    } else if (shardCoord) {
      [lng, lat] = shardCoord;
    } else if (Number.isFinite(Number(arc?.lng)) && Number.isFinite(Number(arc?.lat))) {
      lng = Number(arc.lng);
      lat = Number(arc.lat);
    } else if (arc?.geometry) {
      const c = computeGeoJsonCentroid(arc.geometry);
      if (c) [lng, lat] = c;
    }
    if (Number.isFinite(lng) && Number.isFinite(lat)) {
      centroidLngs.push(lng);
      centroidLats.push(lat);
    }

    const isVacant =
      allYears.length === 0 &&
      (isDemolishedOv || isUnimprovedNonExempt || stateClass.startsWith("C"));

    parcels.push({
      hcad,
      found: true,
      isTaxExempt,
      isVacant,
      address,
      owner,
      subdivision,
      legalDescription,
      stateClass,
      imprVal,
      building_name: String(ov?.landmark_name || ov?.building_name || loc?.buildingName || "").trim(),
      landmark_name: String(ov?.landmark_name || loc?.landmarkName || "").trim(),
      historic_district: String(ov?.historic_district || loc?.historicDistrict || "").trim(),
      good_brick_summary: String(ov?.good_brick_summary || loc?.goodBrickSummary || "").trim(),
      designations: Array.isArray(loc?.designations) ? loc.designations.slice() : [],
      overrideYearBuilt: ovPrimaryYr,
      yearBuilt: allYears.length > 0 ? allYears[0] : 0,
      oldestYearBuilt: allYears.length > 0 ? allYears[0] : 0,
      allYears,
      allYearsBuilt: allYears,
      lng,
      lat,
    });
  }

  const centroid =
    centroidLngs.length > 0
      ? [
          Number((centroidLngs.reduce((a, b) => a + b, 0) / centroidLngs.length).toFixed(6)),
          Number((centroidLats.reduce((a, b) => a + b, 0) / centroidLats.length).toFixed(6)),
        ]
      : null;

  return {
    parcels,
    unfoundHcads,
    warnings,
    centroid,
  };
}

/**
 * Single canonical Pass 1 rule-based plat classifier shared identically with Python classify_item().
 *
 * Why blank-prevSub checks hasPreservationRiskSignal: Surveyors frequently omit Previous Subdivision
 * on commercial replats and variance requests (e.g. Amerimex Motors 2026-1659 in Section D. Variances).
 * Blank prevSub only screens an item out when zero preservation risk or variance/hearing signals exist.
 */
export function classifyPlatItem(it = {}, reviewOverride = null) {
  const typeStr = String(it.type || "");
  const code = parseTypeCode(typeStr);
  const lu = String(it.landUse || "");
  const ac = parseFiniteNum(it.acreage);
  const lots = parseFiniteNum(it.lots);
  const cityVal = String(it.city || "");
  const inCity = /city/i.test(cityVal) && !/^etj$/i.test(cityVal.trim());
  const sec = String(it.section || "").trim();
  const special = String(it.special || "");
  const name = String(it.name || "");
  const prevSub = String(it.prevSub || "").trim();
  const at = it.atlas && typeof it.atlas === "object" ? it.atlas : {};

  const flags = [];
  if (
    code === "C3N" ||
    code === "PH" ||
    /^c\./i.test(sec) ||
    /^[ivx]+\.\s*establish\b/i.test(sec)
  ) {
    flags.push("hearing");
  }
  if (code === "DPV" || /dpv/i.test(typeStr) || /^k\./i.test(sec)) {
    flags.push("dpv");
  }
  if (/variance/i.test(special) || code === "DPV" || /^(?:d|k)\./i.test(sec)) {
    flags.push("variance");
  }
  if (/reconsideration/i.test(special) || /^f\./i.test(sec)) {
    flags.push("reconsider");
  }
  if (it.deferral) {
    flags.push("deferred");
  }

  if (prevSub) {
    if (ac !== null && ac <= 0.35 && lots !== null && lots >= 2 && lots <= 4) {
      flags.push("split");
    }
    if (/unrestricted|commercial|multifamily|multi-unit/i.test(lu)) {
      flags.push("use");
    }
    if (lots === 0 && !UTILITY_RE.test(lu) && !UTILITY_RE.test(name)) {
      flags.push("reserve");
    }
  }

  if (/shared driveway|pae/i.test(lu)) {
    flags.push("shared");
  }

  const combinedName = `${name} ${prevSub}`.replace(/school lands?/gi, "");
  if (/church|school|institutional/i.test(lu) || INST_RE.test(combinedName)) {
    flags.push("inst");
  }

  if (!inCity && ac !== null && ac >= 20) {
    flags.push("tract");
  }

  const revYearNum = reviewOverride?.yearBuilt ? parseFiniteNum(reviewOverride.yearBuilt) : null;
  const atlasYearNum = parseFiniteNum(at.earliestYearBuilt);
  const validYears = [revYearNum, atlasYearNum].filter(
    (y) => y !== null && y >= 1836 && y <= 2035
  );
  const parcelYr = validYears.length > 0 ? Math.min(...validYears) : 0;

  if (parcelYr >= 1836 && parcelYr <= 1976) {
    flags.push("pre1976");
  }
  if (at.hasActivePermit) {
    flags.push("permit");
  }
  if (Array.isArray(at.smlsProtections) && at.smlsProtections.length > 0) {
    flags.push("smls");
  }

  const atlasDesigs = Array.isArray(at.designations) ? at.designations.filter(Boolean) : [];
  const hdFromDesig = atlasDesigs.find((d) => /historic district/i.test(String(d))) || "";
  const hdName = String(it.hd || at.historicDistrict || hdFromDesig || "").trim();
  const hasAnyDesignation = Boolean(hdName || atlasDesigs.length > 0);
  const hasRiskFlag = flags.some(
    (f) =>
      f === "dpv" ||
      f === "hearing" ||
      f === "variance" ||
      f === "reconsider" ||
      f === "pre1976" ||
      f === "permit" ||
      f === "inst"
  );
  const hasPreservationSignal = hasAnyDesignation || hasRiskFlag;

  let group = "check";
  let reason = "";
  if (hdName) {
    group = "designated";
    const cleanHd = hdName.replace(/^City historic district\s*\((.*)\)$/i, "$1");
    reason = `In a city historic district (${cleanHd}).`;
  } else if (!inCity) {
    group = "etj";
    reason = "Outside city limits (ETJ). Skim for cemeteries, farmsteads or rural churches.";
  } else if (code === "SP" && !hasPreservationSignal) {
    group = "out";
    reason = "Street dedication plat.";
  } else if (code === "GP" && !hasPreservationSignal) {
    group = "out";
    reason = "General plan.";
  } else if (!prevSub && !hasPreservationSignal) {
    group = "out";
    reason = "First-time plat: no previous subdivision.";
  } else if (
    UTILITY_RE.test(lu) &&
    !/residential|commercial|unrestricted|multi/i.test(lu) &&
    !hasPreservationSignal
  ) {
    group = "out";
    reason = "Utility site.";
  }

  let overriddenFromOut = false;
  let originalReason = reason;
  if (reviewOverride?.overrideScreenOut && group === "out") {
    group = "check";
    overriddenFromOut = true;
  }

  const weights = {
    dpv: 4,
    pre1976: 4,
    permit: 5,
    hearing: 2,
    variance: 2,
    reconsider: 1,
    deferred: 1,
    split: 3,
    use: 2,
    reserve: 1,
    shared: 1,
    inst: 3,
    tract: 1,
    smls: 1,
  };
  const score = flags.reduce((sum, f) => sum + (weights[f] || 0), 0);

  return {
    group,
    reason,
    originalReason,
    overriddenFromOut,
    flags,
    score,
    code,
    inCity,
  };
}

/**
 * Build a collision-free per-cycle item occurrence key (<cycleIso>::<appNo>).
 */
export function buildOccurrenceKey(cycleKey, appNo) {
  const c = String(cycleKey || "unknown-cycle").trim();
  const a = String(appNo || "").trim();
  return `${c}::${a}`;
}

export function parseOccurrenceKey(occKey = "") {
  const s = String(occKey || "");
  const idx = s.indexOf("::");
  if (idx < 0) return { cycleKey: "", appNo: s };
  return {
    cycleKey: s.slice(0, idx),
    appNo: s.slice(idx + 2),
  };
}

/**
 * Normalize a plat name for cross-cycle continuity while strictly preserving Section / Phase
 * numbers ("OAK ESTATES SEC 1" vs "OAK ESTATES SEC 2") and rejecting generic reserve names.
 */
export function normalizeContinuityPlatName(rawName = "") {
  let s = String(rawName || "").toUpperCase().trim();
  if (!s) return "";
  s = s.replace(/\([^)]*\)/g, " ");
  // Standardize section/phase markers without deleting their numeric/alpha identifiers
  s = s.replace(/\b(?:SECTION|SECT)\b/g, "SEC");
  s = s.replace(/\bPHASE\b/g, "PH");
  // Strip non-identifying procedural suffixes only when they do not carry section numbers
  s = s.replace(/\b(?:PARTIAL\s+REPLAT\s+NO\s*\d+|REPLAT\s+NO\s*\d+|AMENDING\s+PLAT\s+NO\s*\d+)\b/g, " ");
  s = s.replace(/\b(?:PARTIAL\s+REPLAT|REPLAT|AMENDING\s+PLAT|SUBDIVISION|ADDITION)\b/g, " ");
  s = s.replace(/[^A-Z0-9\s]/g, " ");
  s = s.replace(/\s+/g, " ").trim();
  if (!s || s.length < 5) return "";
  if (GENERIC_STANDALONE_PLAT_NAMES.has(s)) return "";
  if (/^(?:RESERVE|TRACT|LOT|BLOCK)\s+[A-Z0-9]+$/.test(s)) return "";
  return s;
}

/**
 * Return ordered real application numbers (e.g. "2026-1718", "DPV-26073533") while skipping
 * cycle-local agenda placeholder IDs like "PH-131" or "DPV-1" (F2-07, DF-5).
 */
export function getCanonicalPlatAppIds(entity = {}) {
  if (!entity || typeof entity !== "object") return [];
  const out = [];
  for (const field of ["app", "appNo", "sourcePlatApp", "linkedAppNo", "id"]) {
    let raw = String(entity[field] || "").trim().toUpperCase();
    if (!raw) continue;
    if (field === "id" && !raw.startsWith("PLAT-")) continue;
    if (raw.startsWith("PLAT-")) raw = raw.slice(5);
    if (/^(?:PH-\d+|DPV-\d{1,5})$/i.test(raw)) continue;
    if (!out.includes(raw)) out.push(raw);
  }
  return out;
}

/**
 * Generate ordered cross-cycle continuity keys for matching deferred or withdrawn/resubmitted plats:
 *   1. Exact non-synthetic application number (e.g. "app:2026-1659", "app:DPV-26073533")
 *   2. Exact sorted 13-digit HCAD parcel set (e.g. "hcad:0231440000010")
 *   3. Section-preserving non-generic plat name corroborated by Super Neighborhood or Applicant/Developer
 */
export function buildContinuityKeys(it = {}, rev = {}) {
  const keys = [];
  for (const app of getCanonicalPlatAppIds(it)) {
    keys.push(`app:${app}`);
  }

  const rawHcads = [rev?.hcad || "", it.hcad || "", it.atlas?.hcad_num || ""].join(" ");
  const { hcadIds } = parseHcadAccountList(rawHcads);
  if (hcadIds.length > 0) {
    const sortedHcads = hcadIds.slice().sort().join(",");
    keys.push(`hcad:${sortedHcads}`);
  }

  const normName = normalizeContinuityPlatName(it.name || "");
  const geoOrParty = String(
    it.superN || it.atlas?.superNeighborhoodMatch || it.developer || it.applicant || ""
  )
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, " ")
    .trim();
  if (normName && geoOrParty) {
    keys.push(`name:${normName}|ctx:${geoOrParty}`);
  }

  return keys;
}

function areContinuityNamesCompatible(normA = "", normB = "") {
  if (!normA || !normB) return true;
  const secA = (normA.match(/\b(?:SEC|PH)\s+([A-Z0-9]+)\b/) || [])[1] || "";
  const secB = (normB.match(/\b(?:SEC|PH)\s+([A-Z0-9]+)\b/) || [])[1] || "";
  if (secA && secB && secA !== secB) return false;
  if (normA === normB || normA.includes(normB) || normB.includes(normA)) return true;
  const ta = normA.split(/\s+/).filter((t) => t.length >= 3);
  const tb = normB.split(/\s+/).filter((t) => t.length >= 3);
  if (ta.length === 0 || tb.length === 0) return false;
  const setB = new Set(tb);
  let shared = 0;
  for (const tok of new Set(ta)) {
    if (setB.has(tok)) shared += 1;
  }
  const minNeeded = Math.min(ta.length, tb.length) === 1 ? 1 : 2;
  return shared >= minNeeded;
}

export function areContinuitySnapshotsCompatible(curHcads, curNormName, prevHcads, prevNormName) {
  if (curHcads.length > 0 && prevHcads.length > 0) {
    return curHcads.some((id) => prevHcads.includes(id));
  }
  return areContinuityNamesCompatible(curNormName, prevNormName);
}

/**
 * Return true when a plat review why/notes string is blank or pure machine-generated boilerplate.
 * Preserves explicit staff notes ("EA note:", "EA:", "Staff note:", "PH note:") and ignores
 * stray outer punctuation wrappers (e.g. leading parenthesis on boilerplate).
 */
export function isBoilerplatePlatWhy(text = "") {
  const s = String(text ?? "").trim();
  if (!s) return true;
  if (/\b(?:ea\s*note|ea\s*:|staff\s*note|ph\s*note)\b/i.test(s)) {
    return false;
  }
  const sInner = s.replace(/^[\s(\[]+|[\s)\]]+$/g, "");
  if (!sInner) return true;
  return /^(?:Atlas matched HCAD parcel at |Atlas matched designated historic resource |Section K Development Plat Variance |Flagged on Planning Commission agenda |Confirmed from \d{4}-\d{2}-\d{2} Planning Commission Agenda|Proposed vacant-parcel development at )/i.test(
    sInner
  );
}

/**
 * Merge a cloud or browser review overlay onto a fresh pipeline review seed safely (DF-1).
 *
 * Unconfirmed machine auto-enrichments yield stale geocoded fields to fresher static pipeline seeds,
 * and retracted pre-Pass-2 baseline mis-geocodes (DPV-26073533 -> 2603 MILAM ST 15 and
 * 2026-1585 -> 1226 W CLAY ST) are cleanly stripped when untouched by staff.
 */
export function mergePlatReviewOverlay(seedRev = {}, incomingRev = null, item = null) {
  const base = seedRev && typeof seedRev === "object" ? { ...seedRev } : {};
  if (!incomingRev || typeof incomingRev !== "object") return base;
  const inc = { ...incomingRev };
  const appId = String(item?.app || item?.appNo || "").trim().toUpperCase();
  const incAddrUpper = String(inc.address || "")
    .toUpperCase()
    .replace(/[^A-Z0-9\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  const humanFields = Array.isArray(inc.humanEditedFields)
    ? inc.humanEditedFields.map((f) => String(f || "").trim()).filter(Boolean)
    : [];
  const hasHumanFields = inc.humanEdited === true || humanFields.length > 0;
  const hasHumanWhy = Boolean(inc.why && !isBoilerplatePlatWhy(inc.why));
  const staleMarkers = RETRACTED_BASELINE_PLAT_SEEDS[appId] || [];
  const hasStaleSeed =
    appId in RETRACTED_BASELINE_PLAT_SEEDS &&
    staleMarkers.some((m) => incAddrUpper.includes(m));

  if (appId in RETRACTED_BASELINE_PLAT_SEEDS && !hasHumanWhy && !hasHumanFields) {
    if (hasStaleSeed || !inc.updated) {
      return base;
    }
  }

  const isConfirmedHuman = Boolean(inc.tierConfirmed) || hasHumanFields || hasHumanWhy;
  if (!isConfirmedHuman && !inc.overrideScreenOut) {
    const out = { ...base };
    for (const [k, v] of Object.entries(inc)) {
      if (v === null || v === undefined || v === "") continue;
      if (
        (k === "address" ||
          k === "yearBuilt" ||
          k === "designation" ||
          k === "hcad" ||
          k === "why" ||
          k === "sources" ||
          k === "followup") &&
        out[k]
      ) {
        continue;
      }
      if (k === "tier" && isBoilerplatePlatWhy(inc.why) && (inc.autoEnriched === true || !inc.updated)) {
        continue;
      }
      out[k] = v;
    }
    return out;
  }

  const out = { ...base, ...inc };
  if (!hasHumanFields && !hasHumanWhy && hasStaleSeed) {
    for (const f of ["address", "yearBuilt", "designation", "hcad", "why", "sources", "followup"]) {
      if (base[f] !== undefined) out[f] = base[f];
    }
  }
  return out;
}

/**
 * Link deferred, reconsidered, or withdrawn-and-resubmitted items across chronologically
 * ordered Planning Commission agenda cycles, carrying forward complete final human-edited
 * fields and healing legacy keystroke-prefix truncations (F2-09, DF-1, DF-8).
 */
export function linkCrossCycleHistory(cyclesMap = {}, cycleOrder = []) {
  const ordered =
    Array.isArray(cycleOrder) && cycleOrder.length > 0
      ? cycleOrder.slice().sort()
      : Object.keys(cyclesMap || {}).sort();

  const seenByKey = new Map();

  for (const cycleIso of ordered) {
    const cyc = cyclesMap[cycleIso];
    if (!cyc || !Array.isArray(cyc.items)) continue;
    if (!cyc.reviews || typeof cyc.reviews !== "object") cyc.reviews = {};
    const reviews = cyc.reviews;

    for (const it of cyc.items) {
      const r = reviews[it.app] || {};
      let cKeys = buildContinuityKeys(it, r);
      it.continuityKey = cKeys[0] || "";
      const curHcads = parseHcadAccountList(
        [r.hcad || "", it.hcad || "", it.atlas?.hcad_num || ""].join(" ")
      ).hcadIds;
      const curNormName = normalizeContinuityPlatName(it.name || "");

      const priorMap = new Map();
      for (const ck of cKeys) {
        const prevList = seenByKey.get(ck) || [];
        for (const prev of prevList) {
          if (prev.cycleKey === cycleIso || priorMap.has(`${prev.cycleKey}::${prev.app}`)) {
            continue;
          }
          if (ck.startsWith("app:")) {
            const prevHcads = parseHcadAccountList(prev.hcad || "").hcadIds;
            const prevNormName = normalizeContinuityPlatName(prev.name || "");
            if (!areContinuitySnapshotsCompatible(curHcads, curNormName, prevHcads, prevNormName)) {
              continue;
            }
          }
          priorMap.set(`${prev.cycleKey}::${prev.app}`, prev);
        }
      }

      const priorCycles = Array.from(priorMap.values()).sort((a, b) =>
        a.cycleKey.localeCompare(b.cycleKey)
      );
      it.priorCycles = priorCycles;

      if (priorCycles.length > 0) {
        const latestPrior = priorCycles[priorCycles.length - 1];
        const humanFields = new Set(
          Array.isArray(r.humanEditedFields) ? r.humanEditedFields.map(String) : []
        );
        const carriedFields = new Set(
          Array.isArray(r.carriedFields) ? r.carriedFields.map(String) : []
        );
        const priorIsHuman = Boolean(latestPrior.isHumanReviewed || latestPrior.tierConfirmed);
        const curIsUntouchedAuto = Boolean(
          r.autoEnriched === true &&
            !humanFields.size &&
            !r.tierConfirmed &&
            isBoilerplatePlatWhy(r.why)
        );
        let changedCarry = false;

        for (const field of CARRY_FORWARD_REVIEW_FIELDS) {
          if (humanFields.has(field)) continue;
          const priorVal = String(latestPrior[field] || "").trim();
          if (!priorVal) continue;
          const curVal = String(r[field] || "").trim();
          const itemHasOwnHcad = field === "hcad" && Boolean(String(it.hcad || "").trim());
          let shouldUpdate = false;

          if (
            (!curVal && !itemHasOwnHcad) ||
            (field === "designation" && curVal === "Not checked yet" && priorVal !== "Not checked yet")
          ) {
            shouldUpdate = true;
          } else if (carriedFields.has(field) && curVal !== priorVal) {
            shouldUpdate = true;
          } else if (priorIsHuman && curIsUntouchedAuto && curVal !== priorVal) {
            shouldUpdate = true;
          } else if (curVal && curVal !== priorVal) {
            if (field === "hcad") {
              const curSet = parseHcadAccountList(curVal).hcadIds;
              const priorSet = parseHcadAccountList(priorVal).hcadIds;
              if (
                curSet.length > 0 &&
                curSet.length < priorSet.length &&
                curSet.every((id) => priorSet.includes(id))
              ) {
                shouldUpdate = true;
              }
            } else if (
              field === "address" ||
              field === "yearBuilt" ||
              field === "why" ||
              field === "designation" ||
              field === "sources" ||
              field === "followup"
            ) {
              if (
                priorVal.toUpperCase().startsWith(curVal.toUpperCase()) &&
                curVal.length < priorVal.length
              ) {
                shouldUpdate = true;
              }
            }
          }

          if (shouldUpdate) {
            r[field] = priorVal;
            carriedFields.add(field);
            r.carriedFromCycle = latestPrior.cycleKey;
            changedCarry = true;
          }
        }

        if (changedCarry) {
          r.carriedFields = CARRY_FORWARD_REVIEW_FIELDS.filter((f) => carriedFields.has(f));
          reviews[it.app] = r;
          it._c = classifyPlatItem(it, r);
          cKeys = buildContinuityKeys(it, r);
          it.continuityKey = cKeys[0] || "";
        }
      }

      const snapshot = {
        cycleKey: cycleIso,
        app: it.app,
        linkedAppNo: it.linkedAppNo || "",
        name: it.name || "",
        deferral: it.deferral || "",
        section: it.section || "",
        onMeetingAgenda: it.onMeetingAgenda !== false,
        tier: r.tier || "",
        tierConfirmed: Boolean(r.tierConfirmed),
        hcad: r.hcad || it.hcad || "",
        address: r.address || it.atlas?.primaryAddress || "",
        yearBuilt: r.yearBuilt || (it.atlas?.earliestYearBuilt ? String(it.atlas.earliestYearBuilt) : ""),
        designation: r.designation || "",
        threatCategory: r.threatCategory || it.threatCategory || "",
        why: r.why || "",
        sources: r.sources || "",
        followup: r.followup || "",
      };

      for (const ck of cKeys) {
        if (!seenByKey.has(ck)) seenByKey.set(ck, []);
        seenByKey.get(ck).push(snapshot);
      }
    }
  }

  return cyclesMap;
}

const DEVELOPER_PLACEHOLDER_RE =
  /^(?:SEE\s+HCAD(?:\s+PROPERTY\s+SEARCH)?|UNKNOWN|NONE(?:\s+FOUND)?|N\/?A|NOT\s+RECORDED|VARIOUS(?:\s+OWNERS)?|OWNER|CURRENT\s+OWNER|TBD)$/i;
const DEVELOPER_GENERIC_ONLY = new Set([
  "HOUSTON",
  "TEXAS",
  "HARRIS",
  "HARRIS COUNTY",
  "CITY OF HOUSTON",
  "STATE OF TEXAS",
  "COUNTY OF HARRIS",
  "DEVELOPMENT",
  "DEVELOPMENTS",
  "HOLDINGS",
  "HOLDING",
  "INVESTMENTS",
  "INVESTMENT",
  "PROPERTIES",
  "PROPERTY",
  "REALTY",
  "REAL ESTATE",
  "HOMES",
  "BUILDERS",
  "BUILDING",
  "GROUP",
  "PARTNERS",
  "PARTNERSHIP",
  "VENTURES",
  "ENTERPRISES",
  "MANAGEMENT",
  "CAPITAL",
  "TRUST",
  "ESTATE",
  "ESTATES",
  "ASSOCIATES",
]);
const DEVELOPER_CORP_SUFFIX_RE =
  /\s+\b(?:P\.?\s*L\.?\s*L\.?\s*C\.?|L\.?\s*L\.?\s*C\.?|L\.?\s*L\.?\s*P\.?|L\.?\s*P\.?|LIMITED\s+PARTNERSHIP|LIMITED|LTD\.?|INCORPORATED|INC\.?|CORPORATION|CORP\.?|COMPANY|CO\.?)\s*$/i;

/**
 * Normalize a developer or property owner entity name for exact cross-item Watchlist attribution,
 * stripping parenthetical notes and corporate entity suffixes without over-stripping root words (F2-08, DF-9).
 */
export function normalizeDeveloperEntityKey(rawName = "") {
  let s = String(rawName || "")
    .replace(/\([^)]*\)/g, " ")
    .trim();
  if (!s || DEVELOPER_PLACEHOLDER_RE.test(s)) return "";
  s = s
    .toUpperCase()
    .replace(/&/g, " AND ")
    .replace(/[.,'"`]/g, " ")
    .replace(/[^A-Z0-9\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (!s || DEVELOPER_PLACEHOLDER_RE.test(s)) return "";

  let prev = "";
  while (s && s !== prev) {
    prev = s;
    const stripped = s.replace(DEVELOPER_CORP_SUFFIX_RE, "").trim();
    if (stripped.length >= 4) {
      s = stripped;
    }
  }
  if (s.length < 4 || DEVELOPER_GENERIC_ONLY.has(s)) return "";
  return s;
}

function splitDeveloperEntities(raw = "") {
  const base = String(raw || "").replace(/\([^)]*\)/g, " ");
  return base
    .split(/[;,/]+/)
    .map((part) => part.trim())
    .filter(Boolean);
}

/**
 * Build a dynamically recomputed Map<normalizedDevKey, sourceDescriptor[]> of active watched
 * developers from:
 *   1. Active (non-Tier-3, non-Archived, non-Tombstone) Watchlist properties (`developer` & `ownerOfRecord` ONLY;
 *      never `buyerRelatedEntities` / `applicant` / `designFirm` platting consultants per DF-9).
 *   2. Staff-confirmed (`tierConfirmed === true`) Tier 1 / Tier 2 cycle items across active `cycleOrder` keys,
 *      skipping items whose latest cycle evaluation downgraded them to Tier 3 (F2-08).
 */
export function buildWatchedDeveloperMap({
  cycles = {},
  cycleOrder = [],
  watchedProperties = [],
  isTombstoneFn = null,
} = {}) {
  const map = new Map();
  const addEntry = (rawPart, descriptor) => {
    const key = normalizeDeveloperEntityKey(rawPart);
    if (!key) return;
    if (!map.has(key)) map.set(key, []);
    const bucket = map.get(key);
    const dup = bucket.some(
      (d) =>
        d.sourceType === descriptor.sourceType &&
        d.watchId === descriptor.watchId &&
        d.cycleKey === descriptor.cycleKey &&
        d.appNo === descriptor.appNo
    );
    if (!dup) bucket.push({ ...descriptor, rawName: rawPart, entityKey: key });
  };

  for (const wp of Array.isArray(watchedProperties) ? watchedProperties : []) {
    if (!wp || typeof wp !== "object") continue;
    if (typeof isTombstoneFn === "function" && isTombstoneFn(wp)) continue;
    const status = String(wp.trackerStatus || "").trim();
    if (
      wp.removedFromWatchlist === true ||
      status === "Archived (Tier 3)" ||
      status === "Removed (Tier 3)" ||
      status === "Removed from Watchlist" ||
      String(wp.tier || "") === "3"
    ) {
      continue;
    }
    const label = String(wp.propertyName || wp.address || wp.id || "Watched Property").trim();
    for (const rawField of [wp.developer || "", wp.ownerOfRecord || ""]) {
      for (const ent of splitDeveloperEntities(rawField)) {
        addEntry(ent, {
          sourceType: "watchlist",
          label,
          address: String(wp.address || "").trim(),
          watchId: String(wp.id || ""),
          cycleKey: "",
          appNo: String(wp.appNo || ""),
          tier: String(wp.tier || "1"),
        });
      }
    }
  }

  const activeCycleKeys = (
    Array.isArray(cycleOrder) && cycleOrder.length > 0
      ? cycleOrder.filter((k) => cycles && Object.prototype.hasOwnProperty.call(cycles, k))
      : Object.keys(cycles || {})
  )
    .slice()
    .sort();

  // Determine the latest cycle tier per continuity key / canonical app ID so a later Tier 3
  // downgrade automatically clears earlier-cycle developer badges.
  const latestTierByKey = new Map();
  for (const ck of activeCycleKeys) {
    const cyc = cycles[ck];
    if (!cyc || !Array.isArray(cyc.items)) continue;
    const revs = cyc.reviews || {};
    for (const it of cyc.items) {
      const r = revs[it.app] || {};
      const t = String(r.tier || "").trim();
      if (!t) continue;
      if (it.continuityKey) latestTierByKey.set(it.continuityKey, t);
      for (const appId of getCanonicalPlatAppIds(it)) {
        latestTierByKey.set(`app:${appId}`, t);
      }
    }
  }

  for (const ck of activeCycleKeys) {
    const cyc = cycles[ck];
    if (!cyc || !Array.isArray(cyc.items)) continue;
    const revs = cyc.reviews || {};
    for (const it of cyc.items) {
      const r = revs[it.app] || {};
      const tier = String(r.tier || "").trim();
      if (r.tierConfirmed !== true || (tier !== "1" && tier !== "2")) continue;
      const latestContTier = it.continuityKey ? latestTierByKey.get(it.continuityKey) : "";
      const appIds = getCanonicalPlatAppIds(it);
      const latestAppTier = appIds.map((id) => latestTierByKey.get(`app:${id}`)).find(Boolean) || "";
      if (latestContTier === "3" || latestAppTier === "3") continue;
      for (const ent of splitDeveloperEntities(it.developer || "")) {
        addEntry(ent, {
          sourceType: "cycle",
          label: String(r.address || it.atlas?.primaryAddress || it.name || it.app).trim(),
          address: String(r.address || it.atlas?.primaryAddress || "").trim(),
          watchId: "",
          cycleKey: ck,
          appNo: String(it.app || ""),
          tier,
        });
      }
    }
  }

  return map;
}

/**
 * Dynamically extract a Section III / Section IV future public hearing date from an Agenda
 * Outline line (e.g. "IV. Establish a public hearing date of November 12, 2026") or Notice text.
 */
export function extractHearingDateIso(text = "") {
  const s = String(text || "");
  if (!s) return "";
  const secMatch = s.match(
    /Establish\s+a?\s*public\s+hearing\s+date\s+(?:of|for)\s+([A-Za-z]+\s+\d{1,2},?\s+\d{4})/i
  );
  if (secMatch) {
    return excelSerialToIso(secMatch[1]);
  }
  const noticeMatch = s.match(
    /(?:Thursday|Wednesday|Monday|Tuesday|Friday),?\s+([A-Za-z]+\s+\d{1,2},?\s+\d{4})/i
  );
  if (noticeMatch) {
    return excelSerialToIso(noticeMatch[1]);
  }
  return excelSerialToIso(s);
}

/**
 * Determine whether a Watchlist property dossier contains any human-authored research,
 * checklist logs, snowball search terms, Google Drive links, or manual metadata edits.
 *
 * Used to protect staff research when a plat is downgraded from Tier 1/2 to Tier 3:
 * untouched auto-promoted entries hard-delete cleanly, while human-annotated dossiers
 * transition to trackerStatus "Archived (Tier 3)" so zero staff research is ever lost.
 */
export function hasHumanDossierContributions(prop) {
  if (!prop || typeof prop !== "object") return false;

  const id = String(prop.id || "");
  const appRef = String(
    prop.appNo || prop.sourcePlatApp || (id.startsWith("plat-") ? id.slice(5) : "")
  ).trim();
  const isPlatOriginated = id.startsWith("plat-") || Boolean(appRef);
  if (!isPlatOriginated) return true;

  if (prop.humanEdited === true) return true;
  if (String(prop.phPastActions || "").trim()) return true;
  if (String(prop.reportedBy || "").trim()) return true;
  if (String(prop.broker || "").trim()) return true;
  const statusVal = String(prop.trackerStatus || "").trim();
  if (statusVal === "Saved" || statusVal === "Lost" || statusVal === "For Sale") return true;
  if (String(prop.archivedAt || "").trim()) return true;

  if (Array.isArray(prop.driveLinks) && prop.driveLinks.length > 0) return true;
  if (Array.isArray(prop.documents) && prop.documents.length > 0) return true;

  if (prop.searchTerms && typeof prop.searchTerms === "object") {
    const hasExplicitTermsTs = Boolean(prop.searchTermsUpdatedAt);
    for (const [catKey, arr] of Object.entries(prop.searchTerms)) {
      if (!Array.isArray(arr) || arr.length === 0) continue;
      if (catKey === "projectNumbers" && !hasExplicitTermsTs) continue;
      return true;
    }
  }

  const checklist = Array.isArray(prop.checklist) ? prop.checklist : [];
  for (const row of checklist) {
    if (!row || typeof row !== "object") continue;
    if (row.humanStatusEdited === true) return true;
    if (row.status === "Complete" || row.status === "N/A") return true;
    if (Array.isArray(row.links) && row.links.length > 0) return true;
    const checks = Array.isArray(row.checks) ? row.checks : [];
    for (const chk of checks) {
      if (!chk || typeof chk !== "object") continue;
      const src = String(chk.source || "").trim();
      const res = String(chk.result || "").trim();
      const isAutomatedEntry =
        Boolean(chk.automated) ||
        src === "auto-sweep" ||
        src === "plat-seed" ||
        res.startsWith("[AUTO-SWEEP]") ||
        res.startsWith("[PLAT-SEED]");
      // Ignore the initial plat-promotion note seeded onto chk-09 when promoted
      const isInitialPlatPromotionSeed =
        row.id === "chk-09" &&
        (res.startsWith("Confirmed from ") || (Boolean(appRef) && res.startsWith(`${appRef} (`)));
      const hasLinks = Array.isArray(chk.links) && chk.links.length > 0;
      if (!isAutomatedEntry && !isInitialPlatPromotionSeed && (res || hasLinks)) {
        return true;
      }
    }
    if (row.id !== "chk-09" && row.status === "In Progress" && checks.length === 0) {
      return true;
    }
  }

  return false;
}

/**
 * Default verified calendar-year (2020-2025) and 2026 YTD demolition permit benchmarks
 * across City of Houston Permitting Center (SD + 4D), HCAD Countywide Permit Roll
 * (Types 13, 33, 43), and HAHC Historic District COA decisions.
 */
export const DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS = {
  updated_through: "2026-10-28",
  coh_structural_demolitions: [
    {
      period: "2026 YTD",
      date_range: "Jan 1 - Oct 28, 2026",
      total_permits: 1755,
      unique_addresses: 1676,
      net_main_bldg_teardowns: 1634,
      standard_demo_sd: 1519,
      dangerous_bldg_4d: 236,
      residential_1_2_fam: 1349,
      commercial_multifam: 288,
      garage_accessory: 118,
      pool_spa_only: 1,
      canopy_porch_partial: 2,
      excluded_type13_bldg_demo_permits: 289,
      historic_index_matched: 42,
      pre_1940_matched: 27,
      sewer_disconnect_permits: 1829,
    },
    {
      period: "2025",
      date_range: "Jan 1 - Dec 31, 2025",
      total_permits: 2187,
      unique_addresses: 2066,
      net_main_bldg_teardowns: 2021,
      standard_demo_sd: 2021,
      dangerous_bldg_4d: 166,
      residential_1_2_fam: 1680,
      commercial_multifam: 356,
      garage_accessory: 151,
      pool_spa_only: 5,
      canopy_porch_partial: 10,
      excluded_type13_bldg_demo_permits: 397,
      historic_index_matched: 60,
      pre_1940_matched: 33,
      sewer_disconnect_permits: 2156,
    },
    {
      period: "2024",
      date_range: "Jan 1 - Dec 31, 2024",
      total_permits: 2380,
      unique_addresses: 2287,
      net_main_bldg_teardowns: 2161,
      standard_demo_sd: 2220,
      dangerous_bldg_4d: 160,
      residential_1_2_fam: 1848,
      commercial_multifam: 322,
      garage_accessory: 210,
      pool_spa_only: 5,
      canopy_porch_partial: 4,
      excluded_type13_bldg_demo_permits: 353,
      historic_index_matched: 47,
      pre_1940_matched: 31,
      sewer_disconnect_permits: 2150,
    },
  ],
  hcad_countywide_demolitions: [
    {
      year: 2025,
      total_permits: 2791,
      unique_parcels: 2464,
      net_main_bldg_teardowns: 2542,
      residential_type_13: 2287,
      commercial_type_33: 479,
      general_type_43: 25,
      garage_accessory: 143,
      pool_spa_only: 87,
      partial_addition_interior: 19,
      excluded_type11_31_remodel_demo: 463,
      coh_agency_061: 2136,
    },
    {
      year: 2024,
      total_permits: 2869,
      unique_parcels: 2596,
      net_main_bldg_teardowns: 2561,
      residential_type_13: 2452,
      commercial_type_33: 405,
      general_type_43: 12,
      garage_accessory: 203,
      pool_spa_only: 85,
      partial_addition_interior: 20,
      excluded_type11_31_remodel_demo: 482,
      coh_agency_061: 2251,
    },
    {
      year: 2023,
      total_permits: 2592,
      unique_parcels: 2393,
      net_main_bldg_teardowns: 2325,
      residential_type_13: 2218,
      commercial_type_33: 365,
      general_type_43: 9,
      garage_accessory: 136,
      pool_spa_only: 88,
      partial_addition_interior: 43,
      excluded_type11_31_remodel_demo: 503,
      coh_agency_061: 1999,
    },
    {
      year: 2022,
      total_permits: 2566,
      unique_parcels: 2323,
      net_main_bldg_teardowns: 2341,
      residential_type_13: 2208,
      commercial_type_33: 269,
      general_type_43: 89,
      garage_accessory: 108,
      pool_spa_only: 88,
      partial_addition_interior: 29,
      excluded_type11_31_remodel_demo: 490,
      coh_agency_061: 2074,
    },
    {
      year: 2021,
      total_permits: 2416,
      unique_parcels: 2249,
      net_main_bldg_teardowns: 2180,
      residential_type_13: 2159,
      commercial_type_33: 232,
      general_type_43: 25,
      garage_accessory: 123,
      pool_spa_only: 77,
      partial_addition_interior: 36,
      excluded_type11_31_remodel_demo: 531,
      coh_agency_061: 1918,
    },
    {
      year: 2020,
      total_permits: 2096,
      unique_parcels: 1975,
      net_main_bldg_teardowns: 1923,
      residential_type_13: 1897,
      commercial_type_33: 185,
      general_type_43: 14,
      garage_accessory: 91,
      pool_spa_only: 40,
      partial_addition_interior: 42,
      excluded_type11_31_remodel_demo: 397,
      coh_agency_061: 1610,
    },
  ],
  hahc_historic_district_demolitions: [
    {
      period: "2026 YTD",
      total_coa_filings: 155,
      demo_reloc_filings: 8,
      main_res_approved: 0,
      main_res_denied: 2,
      accessory_or_partial_approved: 5,
      summary_note:
        "0 main residential structures approved for demolition (2 denied at 202 E 31st 1/2 St & 528 Columbia St; 4 garages/carports & 1 accessory relocation approved)",
    },
    {
      period: "2025",
      total_coa_filings: 136,
      demo_reloc_filings: 7,
      main_res_approved: 2,
      main_res_denied: 3,
      accessory_or_partial_approved: 2,
      summary_note:
        "3 main residential demolitions/relocations denied (1138 Fugate St, 918 Columbia St, 1108 E 16th St); 2 residential demolitions approved (1207 Tulane St, 11 Montglen Ct); 1 garage & 1 partial rear addition approved",
    },
    {
      period: "2024",
      total_coa_filings: 112,
      demo_reloc_filings: 2,
      main_res_approved: 0,
      main_res_denied: 1,
      accessory_or_partial_approved: 1,
      summary_note:
        "0 main residential structures approved for demolition (1 house demolition denied at 1120 E 14th St; 1 garage demolition approved at 1031 E 16th St)",
    },
  ],
};

/**
 * Resolve annual demolition benchmarks from an active_permit_alerts.json payload,
 * falling back cleanly to DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS when omitted.
 */
export function resolveAnnualDemolitionBenchmarks(payload = null) {
  const candidate =
    payload && typeof payload === "object"
      ? payload.annual_benchmarks && typeof payload.annual_benchmarks === "object"
        ? payload.annual_benchmarks
        : payload
      : null;
  if (!candidate) {
    return DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS;
  }
  const hasCoh =
    Array.isArray(candidate.coh_structural_demolitions) &&
    candidate.coh_structural_demolitions.length > 0;
  const hasHcad =
    Array.isArray(candidate.hcad_countywide_demolitions) &&
    candidate.hcad_countywide_demolitions.length > 0;
  const hasHahc =
    Array.isArray(candidate.hahc_historic_district_demolitions) &&
    candidate.hahc_historic_district_demolitions.length > 0;
  if (!hasCoh && !hasHcad && !hasHahc && !candidate.updated_through) {
    return DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS;
  }
  const mergeRows = (rows, defRows, idField) => {
    const defById = new Map(
      (defRows || []).map((r) => [String(r?.[idField] ?? ""), r])
    );
    const validRows = (rows || []).filter(
      (r) => r && typeof r === "object" && !Array.isArray(r)
    );
    if (validRows.length === 0) return defRows;
    return validRows.map((r) => {
      const defRow = defById.get(String(r?.[idField] ?? "")) || {};
      const definedOverrides = Object.fromEntries(
        Object.entries(r).filter(([, v]) => v !== undefined && v !== null)
      );
      return { ...defRow, ...definedOverrides };
    });
  };
  return {
    updated_through:
      candidate.updated_through || DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS.updated_through,
    coh_structural_demolitions: hasCoh
      ? mergeRows(
          candidate.coh_structural_demolitions,
          DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS.coh_structural_demolitions,
          "period"
        )
      : DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS.coh_structural_demolitions,
    hcad_countywide_demolitions: hasHcad
      ? mergeRows(
          candidate.hcad_countywide_demolitions,
          DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS.hcad_countywide_demolitions,
          "year"
        )
      : DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS.hcad_countywide_demolitions,
    hahc_historic_district_demolitions: hasHahc
      ? mergeRows(
          candidate.hahc_historic_district_demolitions,
          DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS.hahc_historic_district_demolitions,
          "period"
        )
      : DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS.hahc_historic_district_demolitions,
  };
}

/**
 * Format grant-ready plain-text summary of annual (2020-2025) and 2026 YTD demolition
 * permit totals across City of Houston, HCAD Countywide, and HAHC Historic Districts.
 */
export function formatDemolitionBenchmarksGrantText(payload = null) {
  const b = resolveAnnualDemolitionBenchmarks(payload);
  const s = (payload && payload.summary) || {};
  const activeTotal = Number(s.total_flagged_buildings ?? 23);
  const activeCrit = Number(s.critical_count ?? 9);
  const activeWatch = Number(s.watch_count ?? 14);
  const fmt = (n) => Number(n ?? 0).toLocaleString("en-US");
  const cohExcludedSummary = (b.coh_structural_demolitions || [])
    .map((r) => `${fmt(r.excluded_type13_bldg_demo_permits)} excluded in ${r.period}`)
    .join("; ");
  const hcadExcludedVals = (b.hcad_countywide_demolitions || [])
    .map((r) => Number(r.excluded_type11_31_remodel_demo ?? 0))
    .filter((n) => n > 0);
  const hcadExcludedRange = hcadExcludedVals.length
    ? `${fmt(Math.min(...hcadExcludedVals))}-${fmt(Math.max(...hcadExcludedVals))}`
    : "397-531";

  const lines = [
    `HOUSTON BUILDING ATLAS - DEMOLITION & PRESERVATION BENCHMARKS (Through ${b.updated_through || "2026-10-28"})`,
    `Active 45-Day Historic Early-Warning Alerts: ${fmt(activeTotal)} flagged historic properties (${fmt(activeCrit)} Critical Demolition/Sewer Disconnect, ${fmt(activeWatch)} Abatement/Garage Watch)`,
    "",
    "1. City of Houston Permitting Center (Source: City of Houston, Houston Public Works, Building Code Enforcement - Sold Permits Database, Types SD & 4D):",
    `   Screening Note: Restricting to Permit Types SD (Standard Demolition) and 4D (Dangerous Building Demolition) already screens out Permit Type 13 (BUILDING PMT) interior gut/remodel, rear-addition removal, and standard pool fill-in permits (${cohExcludedSummary}).`,
  ];

  for (const r of b.coh_structural_demolitions) {
    lines.push(
      `- ${r.period} (${r.date_range}): ${fmt(r.total_permits)} structural demolition permits across ${fmt(r.unique_addresses)} unique addresses -> ${fmt(r.net_main_bldg_teardowns)} Net Full Main-Building Teardowns after screening out ${fmt(r.garage_accessory)} detached garages/sheds, ${fmt(r.pool_spa_only)} swimming pools, and ${fmt(r.canopy_porch_partial)} canopies/patio covers (${fmt(r.residential_1_2_fam)} residential 1-2 family, ${fmt(r.commercial_multifam)} commercial/multi-family; ${fmt(r.standard_demo_sd)} Standard Demo SD + ${fmt(r.dangerous_bldg_4d)} Dangerous Bldg 4D; ${fmt(r.sewer_disconnect_permits)} pre-demo sewer disconnects; ${fmt(r.historic_index_matched)} matched to curated historic Atlas index including ${fmt(r.pre_1940_matched)} pre-1940)`
    );
  }

  lines.push("");
  lines.push(
    "2. HCAD Countywide CAMA Permit Roll (Source: Harris County Appraisal District Real_acct_owner.zip -> permits.txt, Types 13, 33 & 43):"
  );
  lines.push(
    `   Screening Note: Excludes HCAD Types 11 & 31 (Additions/Remodeling, ${hcadExcludedRange} partial/remodel demos/yr). Because HCAD appraisers log taxable extra-feature removals under codes 13/33, net full main-building teardowns screen out pool/spa (~40-88/yr), partial/addition (~19-43/yr), and detached garage/shed (~91-203/yr) descriptions:`
  );
  for (const r of b.hcad_countywide_demolitions) {
    lines.push(
      `- ${r.year}: ${fmt(r.total_permits)} raw demolition permits across ${fmt(r.unique_parcels)} unique parcels -> ${fmt(r.net_main_bldg_teardowns)} Net Full Main-Building Teardowns after screening out ${fmt(r.garage_accessory)} garages/sheds, ${fmt(r.pool_spa_only)} pools/spas, and ${fmt(r.partial_addition_interior)} partial/addition demos (${fmt(r.residential_type_13)} residential, ${fmt(r.commercial_type_33)} commercial, ${fmt(r.general_type_43)} general; ${fmt(r.coh_agency_061)} in City of Houston)`
    );
  }

  lines.push("");
  lines.push(
    "3. HAHC Demolition & Relocation COA Filings in Designated Historic Districts (Source: City of Houston Planning & Development Dept., HAHC Monthly COA Packets):"
  );
  for (const r of b.hahc_historic_district_demolitions) {
    lines.push(
      `- ${r.period}: ${fmt(r.demo_reloc_filings)} demolition/relocation filings out of ${fmt(r.total_coa_filings)} total COAs (${r.summary_note})`
    );
  }

  return lines.join("\n");
}

/**
 * Build shared 3-table HTML markup, official agency citations, and screening methodology
 * notes for both Option A (plat-monitor.html drawer) and Option B (index.html modal).
 */
export function buildDemolitionBenchmarksTablesHtml(
  payload = null,
  { variant = "modal" } = {}
) {
  const b = resolveAnnualDemolitionBenchmarks(payload);
  const cohRows = b.coh_structural_demolitions || [];
  const hcadRows = b.hcad_countywide_demolitions || [];
  const hahcRows = b.hahc_historic_district_demolitions || [];
  const fmt = (n) => Number(n ?? 0).toLocaleString("en-US");
  const escHtml = (str) =>
    String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  const cohExcludedInline = cohRows
    .map(
      (r) =>
        `<code>${fmt(r.excluded_type13_bldg_demo_permits)}</code> excluded in ${escHtml(r.period)}`
    )
    .join("; ");
  const cohExcludedVals = cohRows
    .map((r) => Number(r.excluded_type13_bldg_demo_permits ?? 0))
    .filter((n) => n > 0);
  const cohExcludedRange = cohExcludedVals.length
    ? `${fmt(Math.min(...cohExcludedVals))}-${fmt(Math.max(...cohExcludedVals))}`
    : "289-397";
  const cohTotalSeries = cohRows
    .map((r) => `<code>${fmt(r.total_permits)}</code> in ${escHtml(r.period)}`)
    .join("; ");
  const cohNetSeries = cohRows
    .map((r) => `<code>${fmt(r.net_main_bldg_teardowns)}</code> in ${escHtml(r.period)}`)
    .join("; ");

  const hcadExcludedVals = hcadRows
    .map((r) => Number(r.excluded_type11_31_remodel_demo ?? 0))
    .filter((n) => n > 0);
  const hcadExcludedRange = hcadExcludedVals.length
    ? `${fmt(Math.min(...hcadExcludedVals))}-${fmt(Math.max(...hcadExcludedVals))}`
    : "397-531";
  const hcadNetTop3 = hcadRows
    .slice(0, 3)
    .map((r) => `<code>${fmt(r.net_main_bldg_teardowns)}</code> in ${escHtml(r.year)}`)
    .join("; ");

  if (variant === "drawer") {
    return `
    <div class="demo-bench-grid">
      <div class="demo-bench-box">
        <h4>1. City of Houston Permitting Center (Structural Building Demolitions, 2024 - 2026 YTD)</h4>
        <p class="sub"><strong>Source Citation:</strong> <em>City of Houston, Houston Public Works, Building Code Enforcement - Sold Permits Database</em> (Permit Type SD Standard Demolition + Permit Type 4D Dangerous Building Demolition). Filtering strictly to SD &amp; 4D already excludes Permit Type 13 (<code>BUILDING PMT</code>) interior wall/remodel, rear-addition removal, and standard pool fill-in permits (${cohExcludedInline}).</p>
        <table class="demo-bench-table">
          <thead>
            <tr>
              <th>Period</th>
              <th>Total (SD+4D)</th>
              <th>Net Main-Bldg Teardowns</th>
              <th>Unique Addrs</th>
              <th>Std (SD)</th>
              <th>Dgr Bldg (4D)</th>
              <th>Res (1-2 Fam)</th>
              <th>Comm / Multi</th>
              <th>Garage / Acc</th>
              <th>Pool / Canopy Only</th>
              <th>Historic Index (Pre-1940)</th>
              <th>Sewer Discs</th>
            </tr>
          </thead>
          <tbody>
            ${cohRows
              .map(
                (r) => `
              <tr>
                <td>${escHtml(r.period)} <span style="font-weight:400;color:var(--muted);font-size:11px">(${escHtml(r.date_range)})</span></td>
                <td><strong>${fmt(r.total_permits)}</strong></td>
                <td style="color:var(--t1);font-weight:700">${fmt(r.net_main_bldg_teardowns)}</td>
                <td>${fmt(r.unique_addresses)}</td>
                <td>${fmt(r.standard_demo_sd)}</td>
                <td>${fmt(r.dangerous_bldg_4d)}</td>
                <td>${fmt(r.residential_1_2_fam)}</td>
                <td>${fmt(r.commercial_multifam)}</td>
                <td>${fmt(r.garage_accessory)}</td>
                <td>${fmt(r.pool_spa_only ?? 0)} pool / ${fmt(r.canopy_porch_partial ?? 0)} canopy</td>
                <td>${fmt(r.historic_index_matched)} (${fmt(r.pre_1940_matched)})</td>
                <td>${fmt(r.sewer_disconnect_permits)}</td>
              </tr>`
              )
              .join("")}
          </tbody>
        </table>

        <h4 style="margin-top:12px">3. HAHC Demolition &amp; Relocation COA Filings in Designated Historic Districts (2024 - 2026 YTD)</h4>
        <table class="demo-bench-table">
          <thead>
            <tr>
              <th>Period</th>
              <th>Total COAs</th>
              <th>Demo / Reloc COAs</th>
              <th>Main Res Approved</th>
              <th>Main Res Denied</th>
              <th>Accessory / Partial Approved</th>
              <th style="text-align:left">Outcome Breakdown</th>
            </tr>
          </thead>
          <tbody>
            ${hahcRows
              .map(
                (r) => `
              <tr>
                <td>${escHtml(r.period)}</td>
                <td>${fmt(r.total_coa_filings)}</td>
                <td><strong>${fmt(r.demo_reloc_filings)}</strong></td>
                <td style="color:${r.main_res_approved === 0 ? "var(--ok)" : "var(--t1)"};font-weight:700">${fmt(r.main_res_approved)}</td>
                <td style="font-weight:700">${fmt(r.main_res_denied)}</td>
                <td>${fmt(r.accessory_or_partial_approved)}</td>
                <td style="text-align:left;white-space:normal;min-width:220px;font-size:11.5px;color:#3C4750">${escHtml(r.summary_note)}</td>
              </tr>`
              )
              .join("")}
          </tbody>
        </table>
      </div>
      <div class="demo-bench-box">
        <h4>2. HCAD Countywide Permit Roll (All of Harris County, 2020 - 2025 Calendar Years)</h4>
        <p class="sub"><strong>Source Citation:</strong> <em>Harris County Appraisal District (HCAD) Real Property CAMA Permit Roll</em> (<code>Real_acct_owner.zip -&gt; permits.txt</code>, Types 13 Residential Demo, 33 Commercial Demo &amp; 43 General Demo; excludes Types 11 &amp; 31 Remodel/Addition permits). Because HCAD appraisers log taxable extra-feature removals under codes 13/33, <strong>Net Main-Bldg Teardowns</strong> screens out pool/spa, partial/addition, and detached garage/shed descriptions. Note: HCAD batch-loads current-year City of Houston (061) permits during winter roll prep; use Table 1 for 2026 YTD.</p>
        <table class="demo-bench-table">
          <thead>
            <tr>
              <th>Calendar Year</th>
              <th>Raw Total (13/33/43)</th>
              <th>Net Main-Bldg Teardowns</th>
              <th>Unique Parcels</th>
              <th>Res Demo (13)</th>
              <th>Comm Demo (33)</th>
              <th>Gen Demo (43)</th>
              <th>Garages / Pools / Partial</th>
              <th>City of Houston (061)</th>
            </tr>
          </thead>
          <tbody>
            ${hcadRows
              .map(
                (r) => `
              <tr>
                <td>${escHtml(r.year)}</td>
                <td><strong>${fmt(r.total_permits)}</strong></td>
                <td style="color:var(--t1);font-weight:700">${fmt(r.net_main_bldg_teardowns)}</td>
                <td>${fmt(r.unique_parcels)}</td>
                <td>${fmt(r.residential_type_13)}</td>
                <td>${fmt(r.commercial_type_33)}</td>
                <td>${fmt(r.general_type_43)}</td>
                <td style="font-size:11.5px;color:#3C4750">${fmt(r.garage_accessory ?? 0)} gar / ${fmt(r.pool_spa_only ?? 0)} pool / ${fmt(r.partial_addition_interior ?? 0)} part</td>
                <td>${fmt(r.coh_agency_061)}</td>
              </tr>`
              )
              .join("")}
          </tbody>
        </table>
        <div style="margin-top:10px;padding:9px 11px;background:#F8FAFC;border:1px solid #CBD5E1;border-radius:6px;font-size:11.5px;line-height:1.45;color:#334155">
          <strong>Methodology &amp; Screening Notes (Pool, Partial/Addition &amp; Garage Demolitions):</strong>
          <ul style="margin:4px 0 0 16px;padding:0">
            <li><strong>City of Houston (Table 1):</strong> Interior gut/remodels (<code>DEMO OF INTERIOR NON-LOAD BEARING WALLS</code>), rear-addition removals for remodels (<code>RESIDENTIAL DEMO FOR FUTURE REMODEL</code>), and standard swimming pool fill-ins (<code>EXISTING POOL DEMO 2021 IBC</code>) are issued under <strong>Building Permits (Type 13)</strong> rather than Demolition Permits (Types SD/4D), so <code>${cohExcludedRange}</code> remodel/pool permits per year are already excluded. Within SD+4D, detached garages/sheds (<code>118-210/yr</code>) and the rare sewer-connected pool (<code>1-5/yr</code>) or commercial canopy (<code>2-10/yr</code>) are broken out separately so you can cite either <strong>Total Structural Demolition Permits</strong> (${cohTotalSeries}) or <strong>Net Full Main-Building Teardowns</strong> (${cohNetSeries}).</li>
            <li><strong>HCAD Countywide (Table 2):</strong> Excludes Remodel/Addition permit codes (Types 11 &amp; 31, <code>${hcadExcludedRange}/yr</code>) and screens out <code>40-88</code> pool/spa removals, <code>19-43</code> partial/addition demos, and <code>91-203</code> detached garage/shed demos per year to yield <strong>Net Countywide Full Main-Building Teardowns</strong> (${hcadNetTop3}).</li>
          </ul>
        </div>
      </div>
    </div>`;
  }

  return `
      <div class="pw-bench-tables-grid">
        <div class="pw-bench-table-card">
          <div class="pw-bench-table-title">1. City of Houston Permitting Center (Structural Building Demolitions, 2024 - 2026 YTD)</div>
          <div class="pw-bench-table-sub"><strong>Source Citation:</strong> <em>City of Houston, Houston Public Works, Building Code Enforcement - Sold Permits Database</em> (Permit Type SD Standard Demolition + Permit Type 4D Dangerous Building Demolition). Filtering strictly to SD &amp; 4D already excludes Permit Type 13 (<code>BUILDING PMT</code>) interior remodel, rear-addition removal, and standard pool fill-in permits (${cohExcludedInline}).</div>
          <div class="pw-bench-table-scroll">
            <table class="pw-bench-table">
              <thead>
                <tr>
                  <th>Period</th>
                  <th>Total (SD+4D)</th>
                  <th>Net Main-Bldg Teardowns</th>
                  <th>Unique Addrs</th>
                  <th>Std (SD)</th>
                  <th>Dgr Bldg (4D)</th>
                  <th>Res (1-2 Fam)</th>
                  <th>Comm / Multi</th>
                  <th>Garage / Acc</th>
                  <th>Pool / Canopy Only</th>
                  <th>Historic Index (Pre-1940)</th>
                  <th>Sewer Discs</th>
                </tr>
              </thead>
              <tbody>
                ${cohRows
                  .map(
                    (r) => `
                  <tr>
                    <td><strong>${escHtml(r.period)}</strong> <span class="pw-bench-range">(${escHtml(r.date_range)})</span></td>
                    <td class="mono"><strong>${fmt(r.total_permits)}</strong></td>
                    <td class="mono" style="color:#FCA5A5;font-weight:700;">${fmt(r.net_main_bldg_teardowns)}</td>
                    <td class="mono">${fmt(r.unique_addresses)}</td>
                    <td class="mono">${fmt(r.standard_demo_sd)}</td>
                    <td class="mono">${fmt(r.dangerous_bldg_4d)}</td>
                    <td class="mono">${fmt(r.residential_1_2_fam)}</td>
                    <td class="mono">${fmt(r.commercial_multifam)}</td>
                    <td class="mono">${fmt(r.garage_accessory)}</td>
                    <td class="mono">${fmt(r.pool_spa_only ?? 0)} pool / ${fmt(r.canopy_porch_partial ?? 0)} canopy</td>
                    <td class="mono">${fmt(r.historic_index_matched)} (${fmt(r.pre_1940_matched)})</td>
                    <td class="mono">${fmt(r.sewer_disconnect_permits)}</td>
                  </tr>`
                  )
                  .join("")}
              </tbody>
            </table>
          </div>

          <div class="pw-bench-table-title" style="margin-top:12px;">3. HAHC Demolition &amp; Relocation COA Decisions in Designated Historic Districts (2024 - 2026 YTD)</div>
          <div class="pw-bench-table-scroll">
            <table class="pw-bench-table">
              <thead>
                <tr>
                  <th>Period</th>
                  <th>Total COAs</th>
                  <th>Demo / Reloc COAs</th>
                  <th>Main Res Approved</th>
                  <th>Main Res Denied</th>
                  <th>Accessory / Partial Approved</th>
                  <th style="text-align:left;">Outcome Breakdown</th>
                </tr>
              </thead>
              <tbody>
                ${hahcRows
                  .map(
                    (r) => `
                  <tr>
                    <td><strong>${escHtml(r.period)}</strong></td>
                    <td class="mono">${fmt(r.total_coa_filings)}</td>
                    <td class="mono"><strong>${fmt(r.demo_reloc_filings)}</strong></td>
                    <td class="mono" style="color:${r.main_res_approved === 0 ? "#6EE7B7" : "#FCA5A5"};font-weight:700;">${fmt(r.main_res_approved)}</td>
                    <td class="mono" style="font-weight:700;">${fmt(r.main_res_denied)}</td>
                    <td class="mono">${fmt(r.accessory_or_partial_approved)}</td>
                    <td style="text-align:left;white-space:normal;min-width:210px;color:#CBD5E1;font-size:11px;">${escHtml(r.summary_note)}</td>
                  </tr>`
                  )
                  .join("")}
              </tbody>
            </table>
          </div>
        </div>

        <div class="pw-bench-table-card">
          <div class="pw-bench-table-title">2. HCAD Countywide Permit Roll (All of Harris County, 2020 - 2025 Calendar Years)</div>
          <div class="pw-bench-table-sub"><strong>Source Citation:</strong> <em>Harris County Appraisal District (HCAD) Real Property CAMA Permit Roll</em> (<code>Real_acct_owner.zip -&gt; permits.txt</code>, Types 13 Residential Demo, 33 Commercial Demo &amp; 43 General Demo; excludes Types 11 &amp; 31 Remodel/Addition permits). Because HCAD appraisers log taxable extra-feature removals under codes 13/33, <strong>Net Main-Bldg Teardowns</strong> screens out pool/spa, partial/addition, and detached garage/shed descriptions. Note: HCAD batch-loads current-year City of Houston (061) permits during winter roll prep; use Table 1 for 2026 YTD.</div>
          <div class="pw-bench-table-scroll">
            <table class="pw-bench-table">
              <thead>
                <tr>
                  <th>Year</th>
                  <th>Raw Total (13/33/43)</th>
                  <th>Net Main-Bldg Teardowns</th>
                  <th>Unique Parcels</th>
                  <th>Res Demo (13)</th>
                  <th>Comm Demo (33)</th>
                  <th>Gen Demo (43)</th>
                  <th>Garages / Pools / Partial</th>
                  <th>City of Houston (061)</th>
                </tr>
              </thead>
              <tbody>
                ${hcadRows
                  .map(
                    (r) => `
                  <tr>
                    <td><strong>${escHtml(r.year)}</strong></td>
                    <td class="mono"><strong>${fmt(r.total_permits)}</strong></td>
                    <td class="mono" style="color:#FCA5A5;font-weight:700;">${fmt(r.net_main_bldg_teardowns)}</td>
                    <td class="mono">${fmt(r.unique_parcels)}</td>
                    <td class="mono">${fmt(r.residential_type_13)}</td>
                    <td class="mono">${fmt(r.commercial_type_33)}</td>
                    <td class="mono">${fmt(r.general_type_43)}</td>
                    <td class="mono" style="font-size:11px;color:#CBD5E1;">${fmt(r.garage_accessory ?? 0)} gar / ${fmt(r.pool_spa_only ?? 0)} pool / ${fmt(r.partial_addition_interior ?? 0)} part</td>
                    <td class="mono">${fmt(r.coh_agency_061)}</td>
                  </tr>`
                  )
                  .join("")}
              </tbody>
            </table>
          </div>
          <div style="margin-top:10px;padding:8px 10px;background:rgba(15,23,42,0.55);border:1px solid rgba(148,163,184,0.22);border-radius:6px;font-size:11px;line-height:1.45;color:#CBD5E1;">
            <strong>Screening Methodology (Pools, Partial Remodels &amp; Garages):</strong>
            <div style="margin-top:3px;">• <strong>City of Houston (Table 1):</strong> Interior gut/remodels, rear-addition removals for remodels, and standard pool fill-ins are issued under <strong>Building Permits (Type 13)</strong> rather than Demolition Permits (SD/4D), so <code>${cohExcludedRange}</code> remodel/pool permits/yr are already excluded. Separating detached garages/sheds (<code>118-210/yr</code>) and sewer-connected pools/canopies (<code>3-15/yr</code>) yields <strong>Net Full Main-Building Teardowns</strong> (${cohNetSeries}).</div>
            <div style="margin-top:3px;">• <strong>HCAD Countywide (Table 2):</strong> Excludes Remodel/Addition codes (Types 11 &amp; 31, <code>${hcadExcludedRange}/yr</code>) and screens out <code>40-88</code> pool/spa removals, <code>19-43</code> partial/addition demos, and <code>91-203</code> detached garage/shed demos/yr to yield <strong>Net Countywide Full Main-Building Teardowns</strong> (${hcadNetTop3}).</div>
          </div>
        </div>
      </div>
    `;
}
