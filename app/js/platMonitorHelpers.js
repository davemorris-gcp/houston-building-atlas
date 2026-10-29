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
  { flags = [], superN = "", curatedOverrides = null } = {}
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
    const ov = hcad && overridesMap && overridesMap[hcad] ? overridesMap[hcad] : null;

    const candidateYears = [];
    const ovYr = collectValidYear(rawP.overrideYearBuilt ?? ov?.year_built);
    if (ovYr !== null) {
      // Curated override takes precedence for the parcel's primary historic date
      candidateYears.push(ovYr);
    } else {
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

    const address = String(ov?.address || rawP.address || "").trim();
    const buildingName = String(
      ov?.landmark_name || ov?.building_name || rawP.landmark_name || rawP.building_name || ""
    ).trim();
    const hd = String(ov?.historic_district || rawP.historic_district || "").trim();
    const lm = String(ov?.landmark_name || rawP.landmark_name || "").trim();
    const gb = String(ov?.good_brick_summary || rawP.good_brick_summary || "").trim();

    if (hd) designations.push(`City historic district (${hd})`);
    if (lm) designations.push("City Landmark");
    if (gb) designations.push("Good Brick Award");
    if (Array.isArray(rawP.designations)) {
      for (const d of rawP.designations) {
        if (d) designations.push(String(d));
      }
    }

    parcelSummaries.push({
      hcad,
      address,
      buildingName,
      oldestYearBuilt: parcelOldest,
      allYearsBuilt: dedupYears,
      usedCuratedOverride: ovYr !== null,
    });
  }

  const sortedAllYears = Array.from(new Set(validAllYears)).sort((a, b) => a - b);
  const oldestYearBuilt = sortedAllYears.length > 0 ? sortedAllYears[0] : 0;
  const newestYearBuilt = sortedAllYears.length > 0 ? sortedAllYears[sortedAllYears.length - 1] : 0;
  const uniqueDesignations = Array.from(new Set(designations));
  const flagSet = new Set(Array.isArray(flags) ? flags : []);
  const normSn = String(superN || "").trim().toUpperCase();

  let suggestedTier = "";
  if (uniqueDesignations.length > 0) {
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
    newestYearBuilt,
    allYearsBuilt: sortedAllYears,
    parcels: parcelSummaries,
    designations: uniqueDesignations,
    suggestedTier,
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
 * Generate ordered cross-cycle continuity keys for matching deferred or withdrawn/resubmitted plats:
 *   1. Exact non-synthetic application number (e.g. "app:2026-1659", "app:DPV-26073533")
 *   2. Exact sorted 13-digit HCAD parcel set (e.g. "hcad:0231440000010")
 *   3. Section-preserving non-generic plat name corroborated by Super Neighborhood or Applicant/Developer
 */
export function buildContinuityKeys(it = {}, rev = {}) {
  const keys = [];
  const app = String(it.app || it.appNo || "").trim().toUpperCase();
  const isSyntheticApp = /^(?:PH|DPV)-\d+$/.test(app);
  if (app && !isSyntheticApp) {
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

function areContinuitySnapshotsCompatible(curHcads, curNormName, prevHcads, prevNormName) {
  if (curHcads.length > 0 && prevHcads.length > 0) {
    return curHcads.some((id) => prevHcads.includes(id));
  }
  return areContinuityNamesCompatible(curNormName, prevNormName);
}

/**
 * Link deferred, reconsidered, or withdrawn-and-resubmitted items across chronologically
 * ordered Planning Commission agenda cycles, carrying forward human-entered HCAD IDs,
 * addresses, building years, and review notes without colliding distinct phases or reused
 * COH reference numbers (e.g. 2026-1585 Montrose Vista Replat No 1 vs Montrose Residences).
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
    const reviews = cyc.reviews && typeof cyc.reviews === "object" ? cyc.reviews : {};

    for (const it of cyc.items) {
      const r = reviews[it.app] || {};
      const cKeys = buildContinuityKeys(it, r);
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

      // Carry forward resolved HCAD / human notes onto later unresearched occurrences
      if (priorCycles.length > 0) {
        const latestPrior = priorCycles[priorCycles.length - 1];
        if (!it.hcad && !r.hcad && latestPrior.hcad) {
          r.hcad = latestPrior.hcad;
          reviews[it.app] = r;
        }
        if (!r.address && latestPrior.address) {
          r.address = latestPrior.address;
          reviews[it.app] = r;
        }
        if (!r.yearBuilt && latestPrior.yearBuilt) {
          r.yearBuilt = latestPrior.yearBuilt;
          reviews[it.app] = r;
        }
        if (!r.tier && latestPrior.tier) {
          r.tier = latestPrior.tier;
          r.carriedFromCycle = latestPrior.cycleKey;
          reviews[it.app] = r;
        }
        if (!r.why && latestPrior.why) {
          r.why = latestPrior.why;
          reviews[it.app] = r;
        }
      }

      const snapshot = {
        cycleKey: cycleIso,
        app: it.app,
        name: it.name || "",
        deferral: it.deferral || "",
        section: it.section || "",
        tier: r.tier || "",
        tierConfirmed: Boolean(r.tierConfirmed),
        hcad: r.hcad || it.hcad || "",
        address: r.address || it.atlas?.primaryAddress || "",
        yearBuilt: r.yearBuilt || (it.atlas?.earliestYearBuilt ? String(it.atlas.earliestYearBuilt) : ""),
        why: r.why || "",
      };

      for (const ck of cKeys) {
        if (!seenByKey.has(ck)) seenByKey.set(ck, []);
        seenByKey.get(ck).push(snapshot);
      }
    }
  }

  return cyclesMap;
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
