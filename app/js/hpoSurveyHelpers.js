/**
 * Pure City of Houston Historic Preservation Office (HPO) Historic District parcel survey
 * lookup, COH Individual Landmark / Relocated Structure `WeakMap`-indexed overlay resolution,
 * `#<slug>` campus sub-building and post-1965 replacement-structure isolation guards, and
 * HTML-escaped card builders for `#inspector-hpo-survey-card`, `#boundary-hpo-district-docs-card`,
 * and `.coh-landmark-report-card`.
 */

import { isClaimSuppressed } from "./curatedEdits.js?v=20261109a";
import { escapeHtml, sanitizeHttpUrl } from "./urlValidators.js?v=20261109a";

const STATUS_CODE_LABELS = Object.freeze({
  C: "Contributing",
  PC: "Potentially Contributing",
  NC: "Non-Contributing",
  V: "Vacant",
});

const DISTRICT_NAME_ALIASES = Object.freeze({
  "Courtland Place": "Courtlandt Place Historic District",
  "Courtland Place Historic District": "Courtlandt Place Historic District",
  "Main Street Market Square": "Main Street/Market Square Historic District",
  "Main Street Market Square Historic District": "Main Street/Market Square Historic District",
  "Main St/Market Sq": "Main Street/Market Square Historic District",
  "Main St/Market Sq Historic District": "Main Street/Market Square Historic District",
  "Main St Market Sq": "Main Street/Market Square Historic District",
  "Main St Market Sq Historic District": "Main Street/Market Square Historic District",
  "W. 11th Place": "West Eleventh Place Historic District",
  "W. 11th Place Historic District": "West Eleventh Place Historic District",
  "W 11th Place": "West Eleventh Place Historic District",
  "W 11th Place Historic District": "West Eleventh Place Historic District",
  "W Eleventh Place": "West Eleventh Place Historic District",
  "W Eleventh Place Historic District": "West Eleventh Place Historic District",
  "West 11th Place": "West Eleventh Place Historic District",
  "West 11th Place Historic District": "West Eleventh Place Historic District",
  "Brunner Harmonium": "Brunner-Harmonium Historic District",
  "Brunner Harmonium Historic District": "Brunner-Harmonium Historic District",
  OSW: "Old Sixth Ward Historic District",
  "Old Sixth Ward": "Old Sixth Ward Historic District",
  "Houston Heights Historic District East": "Houston Heights East Historic District",
});

function canonicalizeDistrictSlug(str) {
  return String(str || "")
    .toLowerCase()
    .replace(/\bw\.?\s+(?:11th|eleventh)\b/g, "west eleventh")
    .replace(/\b11th\b/g, "eleventh")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

const NON_ARCHITECTURAL_SURVEY_STYLES = Object.freeze(
  new Set([
    "",
    "n/a",
    "na",
    "-",
    "vacant",
    "lot",
    "parking lot",
    "new",
    "new construction",
    "building",
    "secondary structure",
    "commercial",
  ])
);

const STYLE_GENERIC_PLACEHOLDERS_LOWER = Object.freeze(
  new Set([
    "",
    "residential",
    "historical / architectural structure",
    "houston historic structure",
    "historic structure",
    "structure",
    "none",
    "null",
    "n/a",
    "na",
    "-",
  ])
);

const STYLE_STRUCTURAL_CLASSES_LOWER = Object.freeze(
  new Set([
    "wood or light steel",
    "masonry bearing",
    "open steel skeleton",
    "reinforced concrete",
    "fireproofed steel",
    "mobile home/manufactured housing",
    "storage tank",
    "petrochemical complex",
    "educational campus structure",
    "frame utility shed",
    "carport - residential",
    "canopy - residential",
    "frame detached garage",
    "utility building - metal",
    "portable/modular office - average",
  ])
);

export const HCAD_GRADE_LABELS = Object.freeze({
  "A++": "Luxury Custom Residential (HCAD Grade A++)",
  "A+": "High-Grade Custom Residential (HCAD Grade A+)",
  A: "High-Grade Residential (HCAD Grade A)",
  "A-": "Upper-Grade Residential (HCAD Grade A-)",
  "B+": "Good-Grade Residential (HCAD Grade B+)",
  B: "Good-Grade Residential (HCAD Grade B)",
  "B-": "Above-Average Residential (HCAD Grade B-)",
  "C+": "Average Residential (HCAD Grade C+)",
  C: "Standard Residential (HCAD Grade C)",
  "C-": "Standard Economy Residential (HCAD Grade C-)",
  "D+": "Economy Residential (HCAD Grade D+)",
  D: "Economy Residential (HCAD Grade D)",
  "D-": "Basic Frame Residential (HCAD Grade D-)",
  "E+": "Vernacular Frame Cottage (HCAD Grade E+)",
  E: "Vernacular Frame / Cottage (HCAD Grade E)",
  "E-": "Minimal Frame Structure (HCAD Grade E-)",
  F: "Substandard / Salvage Frame Structure (HCAD Grade F)",
  X: "Tax-Exempt / Institutional Class (HCAD X)",
});

const STYLE_HCAD_GRADES_LOWER = Object.freeze(
  new Set(Object.keys(HCAD_GRADE_LABELS).map((k) => k.toLowerCase()))
);

const STRUCTURAL_CLASS_PATTERN =
  /^(?:canopy\b|carport\b|paving\s*-|utility\s+(?:building|bldg)\b|portable\/modular\b|plant\s+assets\b|petroleum\s+stg\s+tank\b|shed,\s|miscellaneous\s*-|concrete\s+tennis\s+court\b|brick\s+or\s+stone\s+detached\s+garage$|(?:tilt-up|precast|prestressed)\s+concrete$|light\s+steel\b|steel\s+frame$)/i;

/**
 * Return True when `rawVal` matches a known CAMA structural/construction class token
 * (1:1 with `schema.py:is_structural_class_style`).
 */
export function isStructuralClassStyleValue(rawVal) {
  const s = String(rawVal || "").trim();
  return Boolean(
    s && (STYLE_STRUCTURAL_CLASSES_LOWER.has(s.toLowerCase()) || STRUCTURAL_CLASS_PATTERN.test(s))
  );
}

/**
 * Return True when `rawVal` is empty, generic placeholder, HCAD grade, or CAMA structural class
 * (1:1 with `schema.py:is_replaceable_style_value`).
 */
export function isReplaceableStyleValue(rawVal) {
  const low = String(rawVal || "").trim().toLowerCase();
  return (
    STYLE_GENERIC_PLACEHOLDERS_LOWER.has(low) ||
    STYLE_HCAD_GRADES_LOWER.has(low) ||
    isStructuralClassStyleValue(rawVal)
  );
}

/**
 * Return True when `rawVal` can be displayed as a primary style/structural-class chip
 * (excluding blank strings, generic placeholders, and HCAD grade codes).
 */
export function isArchitecturalStyleDisplayCandidate(rawVal) {
  const s = String(rawVal || "").trim();
  if (!s) return false;
  const low = s.toLowerCase();
  return !STYLE_GENERIC_PLACEHOLDERS_LOWER.has(low) && !STYLE_HCAD_GRADES_LOWER.has(low);
}

/**
 * Return True when `buildingYear` indicates a post-1965 modern replacement structure in a
 * Historic District parcel survey (`>25` years newer than `surveyYear`, or replacing an undated
 * Contributing / Potentially Contributing historic survey row).
 */
export function isPostSurveyReplacement(buildingYear, surveyYear, statusCode = "C") {
  const bYr = Number(buildingYear) || 0;
  const sYr = Number(surveyYear) || 0;
  const st = String(statusCode || "NC").toUpperCase();
  if (bYr <= 1965) return false;
  if (sYr > 0 && bYr > sYr + 25) return true;
  return sYr === 0 && (st === "C" || st === "PC" || st === "CONTRIBUTING" || st === "POTENTIALLY CONTRIBUTING");
}

/**
 * Return True when `buildingYear` indicates a post-1965 replacement structure on a standing
 * Individual Landmark or Relocated Structure with a known historical year (`refYear > 0`),
 * preserving post-1965 Modernist and rehabilitated landmarks where `refYear === 0`.
 */
export function isPostLandmarkReplacement(buildingYear, refYear) {
  const bYr = Number(buildingYear) || 0;
  const rYr = Number(refYear) || 0;
  return Boolean(bYr > 1965 && rYr > 0 && bYr > rYr + 25);
}

const STRIP_STREET_TAIL = Object.freeze(
  new Set([
    "HOUSTON",
    "TX",
    "TEXAS",
    "ST",
    "STREET",
    "AVE",
    "AVENUE",
    "BLVD",
    "BOULEVARD",
    "RD",
    "ROAD",
    "DR",
    "DRIVE",
    "LN",
    "LANE",
    "WAY",
    "PKWY",
    "PARKWAY",
    "FWY",
    "FREEWAY",
    "HWY",
    "HIGHWAY",
    "CT",
    "COURT",
    "PL",
    "PLACE",
    "CIR",
    "CIRCLE",
    "SQ",
    "SQUARE",
    "TER",
    "TERRACE",
    "TRL",
    "TRAIL",
    "LOOP",
    "N",
    "S",
    "E",
    "W",
    "NORTH",
    "SOUTH",
    "EAST",
    "WEST",
  ])
);

function buildDistrictCorePdfButtonsHtml(
  docs,
  { inventoryLabel, guidelinesLabel, contextName, guidelinesTitleSuffix = "" }
) {
  const safeName = escapeHtml(contextName || docs.districtName || "");
  const invUrl = sanitizeHttpUrl(docs.inventoryPdfUrl);
  const guideUrl = sanitizeHttpUrl(docs.guidelinesPdfUrl);
  const desigUrl = sanitizeHttpUrl(docs.designationReportUrl);
  const mapUrl = sanitizeHttpUrl(docs.mapPdfUrl);
  const parts = [];
  if (invUrl) {
    parts.push(
      `<a href="${escapeHtml(
        invUrl
      )}" target="_blank" rel="noopener noreferrer" class="hpo-survey-btn" title="Open the official City of Houston Historic Preservation Office parcel inventory PDF for ${safeName}">${inventoryLabel}</a>`
    );
  }
  if (guideUrl) {
    const gTitle = guidelinesTitleSuffix
      ? `Open the adopted City of Houston HAHC Design Guidelines PDF for ${safeName}`
      : "Open the adopted City of Houston HAHC Design Guidelines PDF";
    parts.push(
      `<a href="${escapeHtml(
        guideUrl
      )}" target="_blank" rel="noopener noreferrer" class="hpo-survey-btn secondary" title="${gTitle}">${guidelinesLabel}</a>`
    );
  }
  if (desigUrl && desigUrl !== invUrl) {
    parts.push(
      `<a href="${escapeHtml(
        desigUrl
      )}" target="_blank" rel="noopener noreferrer" class="hpo-survey-btn secondary" title="Open the City of Houston Historic District Designation Report PDF">&#128220; Designation Report (PDF) &#8599;</a>`
    );
  }
  if (mapUrl) {
    parts.push(
      `<a href="${escapeHtml(
        mapUrl
      )}" target="_blank" rel="noopener noreferrer" class="hpo-survey-btn secondary" title="Open the official City of Houston Historic District Boundary &amp; Lot Map PDF">&#128506; Boundary / Lot Map (PDF) &#8599;</a>`
    );
  }
  return parts.join("\n      ");
}

export function isUsableHpoSurveyStyle(rawStyle) {
  const raw = String(rawStyle || "").trim();
  const low = raw.toLowerCase();
  if (low.length < 4 || NON_ARCHITECTURAL_SURVEY_STYLES.has(low)) return false;
  if (raw.includes("  ")) return false;
  if (/\b(?:year|circa)\s+bldg\b/.test(low)) return false;
  if (/^(?:18|19|20)\d{2}\b/.test(low)) return false;
  if (
    low.startsWith("new ") ||
    low.startsWith("vacant") ||
    low.startsWith("parking") ||
    low.startsWith("lot ")
  ) {
    return false;
  }
  return true;
}

const CARDINAL_DIRS = new Set(["N", "S", "E", "W", "NE", "NW", "SE", "SW"]);

function extractLeadingDirectional(normAddr) {
  const pts = String(normAddr || "").split(" ");
  if (pts.length >= 4 && pts[1] === "1/2" && CARDINAL_DIRS.has(pts[2])) {
    return pts[2];
  }
  return pts.length >= 3 && CARDINAL_DIRS.has(pts[1]) ? pts[1] : "";
}

const CITY_STATE_TOKENS = new Set(["HOUSTON", "TX", "TEXAS"]);

/**
 * Normalize a situs street address 1:1 with `harvest_coh_hpo_surveys_and_landmarks.py:normalize_hpo_lookup_address`.
 */
export function normalizeHpoLookupAddress(rawAddr) {
  if (!rawAddr) return "";
  let s = String(rawAddr)
    .replace(/[\u2010-\u2015\u2212\uFE58\uFE63\uFF0D]/g, "-")
    .replace(/\u00bd/g, " 1/2 ")
    .toUpperCase();
  s = s.replace(/\b(?:STE|SUITE|APT|UNIT|BLDG|FL|FLOOR|REAR)\b.*$/, "");
  s = s.replace(/#.*$/, "");
  s = s.replace(/\bELEVENTH\b/g, "11TH");
  s = s.replace(/\b(\d{1,5})(?:\s*-+\s*)?1\/2\b/g, "$1 1/2");
  s = s.replace(/\b(\d+)-([A-D])\b/g, "$1$2");
  s = s.replace(/^\s*(\d{1,5})\s*-+\s*\d{1,5}\b(?!\s*\/)/, "$1");
  s = s.replace(/[^A-Z0-9/\s]/g, " ");
  let parts = s.trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "";
  const firstNum = parts[0].split("/")[0];
  if (!/^\d+[A-Z]?$/.test(firstNum)) return "";
  parts[0] = firstNum;
  while (
    parts.length > 3 &&
    (CITY_STATE_TOKENS.has(parts[parts.length - 1]) || /^\d{5}$/.test(parts[parts.length - 1]))
  ) {
    parts.pop();
  }
  if (
    parts.length >= 3 &&
    parts[1] === "1/2" &&
    !/^\d+(?:ST|ND|RD|TH)?$/.test(parts[2])
  ) {
    parts = [`${parts[0]} 1/2`, ...parts.slice(2)];
  } else if (
    parts.length >= 3 &&
    parts[parts.length - 1] === "1/2" &&
    !parts[0].endsWith(" 1/2") &&
    !/^\d+(?:ST|ND|RD|TH)?$/.test(parts[parts.length - 2])
  ) {
    parts = [`${parts[0]} 1/2`, ...parts.slice(1, -1)];
  }
  const dirMap = { NORTH: "N", SOUTH: "S", EAST: "E", WEST: "W" };
  const trailingDirs = new Set(["N", "S", "E", "W", "NORTH", "SOUTH", "EAST", "WEST"]);
  let capturedTailDir = "";
  while (
    parts.length > 2 &&
    (/^\d+$/.test(parts[parts.length - 1]) || STRIP_STREET_TAIL.has(parts[parts.length - 1]))
  ) {
    const last = parts[parts.length - 1];
    const prev = parts[parts.length - 2];
    if (parts.length === 3 && (prev === "AVE" || prev === "AVENUE") && /^[A-Z]$/.test(last)) {
      break;
    }
    if (
      trailingDirs.has(last) &&
      STRIP_STREET_TAIL.has(prev) &&
      !trailingDirs.has(prev) &&
      !CITY_STATE_TOKENS.has(prev)
    ) {
      capturedTailDir = dirMap[last] || last;
    }
    parts.pop();
  }
  const revDirMap = { N: "NORTH", S: "SOUTH", E: "EAST", W: "WEST" };
  if (parts.length >= 3 && dirMap[parts[1]]) {
    parts[1] = dirMap[parts[1]];
  } else if (parts.length === 2 && revDirMap[parts[1]]) {
    parts[1] = revDirMap[parts[1]];
  }
  if (
    capturedTailDir &&
    parts.length >= 2 &&
    !CARDINAL_DIRS.has(parts[1]) &&
    !dirMap[parts[1]]
  ) {
    parts = [parts[0], capturedTailDir, ...parts.slice(1)];
  }
  const wordAlias = { SAINT: "ST", MOUNT: "MT", AVENUE: "AVE" };
  const normTokens = [parts[0]];
  for (let i = 1; i < parts.length; i += 1) {
    let tok = parts[i];
    if (i + 1 < parts.length) {
      if (parts[i + 1] === "1/2") {
        const mOrd = /^(\d+)(?:ST|ND|RD|TH)$/.exec(tok);
        if (mOrd) tok = mOrd[1];
      } else if (wordAlias[tok]) {
        tok = wordAlias[tok];
      }
    }
    normTokens.push(tok);
  }
  return normTokens.join(" ");
}

/**
 * Generate primary and fallback normalized street address keys (expanding hyphen/slash ranges like
 * `802/804 Commerce St`, `1414-1416 Austin St`, or `3909--3917 S. Main St.` and directional-less
 * variants), 1:1 with `harvest_coh_hpo_surveys_and_landmarks.py:normalize_hpo_street_address_keys`.
 */
export function normalizeHpoStreetAddressKeys(rawAddr) {
  if (!rawAddr) return [];
  const s = String(rawAddr)
    .replace(/[\u2010-\u2015\u2212\uFE58\uFE63\uFF0D]/g, "-")
    .replace(/\u00bd/g, " 1/2 ")
    .replace(/\b(\d{1,5})(?:\s*-+\s*)?1\/2\b/gi, "$1 1/2")
    .trim();
  const keys = [];
  const add = (cand) => {
    const k = normalizeHpoLookupAddress(cand);
    if (k && !keys.includes(k)) keys.push(k);
    if (k) {
      const pts = k.split(" ");
      let noDir = "";
      if (pts.length >= 4 && pts[1] === "1/2" && CARDINAL_DIRS.has(pts[2])) {
        noDir = `${pts[0]} 1/2 ${pts.slice(3).join(" ")}`;
      } else if (pts.length >= 3 && CARDINAL_DIRS.has(pts[1])) {
        noDir = `${pts[0]} ${pts.slice(2).join(" ")}`;
      }
      if (noDir && !keys.includes(noDir)) keys.push(noDir);
    }
  };
  add(s);
  const mRange = /^\s*(\d{1,5})\s*[-/]+\s*(\d{1,5})\s+(.+)$/.exec(s);
  if (mRange) {
    const n1 = mRange[1];
    let n2 = mRange[2];
    const rest = mRange[3];
    if (n2.length < n1.length) {
      n2 = n1.slice(0, n1.length - n2.length) + n2;
    }
    add(`${n1} ${rest}`);
    if (Number(n2) > Number(n1)) {
      add(`${n2} ${rest}`);
    }
  }
  return keys;
}

/**
 * Resolve official HPO inventory, design guidelines, designation report, and boundary map metadata
 * for one of the 23 City of Houston Historic Districts.
 */
export function resolveHpoDistrictDocs(hpoIndex, districtName) {
  if (!hpoIndex || !hpoIndex.districts || !districtName) return null;
  const raw = String(districtName).trim();
  if (!raw || raw === "none" || raw === "Outside Historic District") return null;
  const aliasMap = hpoIndex.district_aliases || DISTRICT_NAME_ALIASES;
  const aliased = aliasMap[raw] || DISTRICT_NAME_ALIASES[raw] || raw;
  const candidates = [aliased];
  if (!/Historic District$/i.test(aliased)) {
    const withHd = `${aliased} Historic District`;
    candidates.push(aliasMap[withHd] || DISTRICT_NAME_ALIASES[withHd] || withHd);
  }
  const districtsDict = hpoIndex.districts;
  let matchedKey = "";
  for (const cand of candidates) {
    if (districtsDict[cand]) {
      matchedKey = cand;
      break;
    }
  }
  if (!matchedKey) {
    const slugTargets = new Set(candidates.map((c) => canonicalizeDistrictSlug(c)));
    for (const k of Object.keys(districtsDict)) {
      if (slugTargets.has(canonicalizeDistrictSlug(k))) {
        matchedKey = k;
        break;
      }
    }
  }
  if (!matchedKey) return null;
  const rec = districtsDict[matchedKey] || {};
  return {
    districtName: matchedKey,
    designationDate: String(rec.designation_date || "").trim(),
    inventoryPdfUrl: sanitizeHttpUrl(rec.inventory_pdf_url),
    designationReportUrl: sanitizeHttpUrl(rec.designation_report_url),
    guidelinesPdfUrl: sanitizeHttpUrl(rec.guidelines_pdf_url),
    mapPdfUrl: sanitizeHttpUrl(rec.map_pdf_url),
    surveyedParcelCount: Number(rec.surveyed_parcel_count) || 0,
    matchedHcadCount: Number(rec.matched_hcad_count) || 0,
    statusCounts: rec.status_counts || {},
  };
}

/**
 * Resolve the official COH HPO Historic District parcel survey entry for an inspected building,
 * enforcing `lost_*` isolation, post-survey replacement-building guards, and 1-click claim
 * suppression (`field: "hpo_survey"`).
 */
export function resolveHpoParcelSurvey(hpoIndex, props = {}, options = {}) {
  if (!hpoIndex || !hpoIndex.items) return null;
  const rawId = String(props.building_id || props.id || "").trim();
  const baseId = rawId.replace(/#(?:fp|aux)_\d+$/, "");
  const isLost =
    Boolean(props.is_lost_houston) ||
    rawId.startsWith("lost_") ||
    String(props.type || "") === "lost_houston";
  if (isLost) return null;

  const hcad = String(props.hcad_num || "").trim();
  const isSubBuilding = baseId.includes("#");
  const rootIdToken = isSubBuilding ? baseId.split("#")[0] : baseId;
  const effectiveHcad = hcad || (/^\d{13}$/.test(rootIdToken) ? rootIdToken : "");
  const suppressKey = effectiveHcad || baseId || rawId;
  const buildingYear = Number(props.year_built) || 0;
  const globalRejections = options.globalRejections || null;

  const itemsDict = hpoIndex.items;
  const distOrder = Array.isArray(hpoIndex.district_order) ? hpoIndex.district_order : [];
  const byHcad = hpoIndex.by_hcad || {};
  const byAddr = hpoIndex.by_addr || {};

  const propDistRaw = String(props.historic_district || "").trim();
  const isExplicitOutside =
    propDistRaw === "Outside Historic District" || /^none$/i.test(propDistRaw);
  if (isExplicitOutside) return null;
  const propDistCanon =
    propDistRaw && !isExplicitOutside
      ? resolveHpoDistrictDocs(hpoIndex, propDistRaw)?.districtName ||
        DISTRICT_NAME_ALIASES[propDistRaw] ||
        propDistRaw
      : "";

  const addrNormTarget = props.address ? normalizeHpoLookupAddress(props.address) : "";
  const addrTargetKeys = props.address ? normalizeHpoStreetAddressKeys(props.address) : [];
  const targetDir = addrNormTarget ? extractLeadingDirectional(addrNormTarget) : "";

  const candidateEntries = [];
  const seenSids = new Set();
  const pushIds = (arr, viaAddr) => {
    if (!Array.isArray(arr)) return;
    for (const rawSid of arr) {
      const sid = String(rawSid);
      if (!seenSids.has(sid)) {
        seenSids.add(sid);
        candidateEntries.push({ sid, viaAddr });
      }
    }
  };

  if (hcad) {
    pushIds(byHcad[hcad], isSubBuilding);
  }
  if (baseId && baseId !== hcad) {
    pushIds(byHcad[baseId], isSubBuilding);
  }
  if (effectiveHcad && effectiveHcad !== hcad && effectiveHcad !== baseId) {
    pushIds(byHcad[effectiveHcad], true);
  }
  for (const ak of addrTargetKeys) {
    if (ak && !ak.startsWith("0 ")) {
      pushIds(byAddr[ak], true);
    }
  }

  let hasAmbiguousDirectionlessAddrCollision = false;
  if (addrNormTarget && !targetDir && Array.isArray(byAddr[addrNormTarget])) {
    const distinctDirs = new Set();
    for (const rawSid of byAddr[addrNormTarget]) {
      const candItem = itemsDict[String(rawSid)];
      if (!candItem) continue;
      const candDist = String(distOrder[Number(candItem.d)] || "").trim();
      if (propDistCanon && candDist.toLowerCase() !== propDistCanon.toLowerCase()) {
        continue;
      }
      const d = extractLeadingDirectional(normalizeHpoLookupAddress(candItem.a));
      if (d) distinctDirs.add(d);
    }
    hasAmbiguousDirectionlessAddrCollision = distinctDirs.size > 1;
  }

  let bestHit = null;
  let bestRank = -1;

  for (const { sid, viaAddr } of candidateEntries) {
    const item = itemsDict[sid];
    if (!item) continue;
    const distName = String(distOrder[Number(item.d)] || "").trim();
    if (!distName) continue;

    if (propDistCanon && distName.toLowerCase() !== propDistCanon.toLowerCase()) {
      continue;
    }

    const surveyYear = Number(item.y) || 0;
    const statusCode = String(item.st || "NC").toUpperCase();
    const isReplacedStructure = isPostSurveyReplacement(buildingYear, surveyYear, statusCode);
    if (viaAddr && isReplacedStructure) {
      continue;
    }

    const statusLabel = STATUS_CODE_LABELS[statusCode] || "Non-Contributing";
    const address = String(item.a || "").trim();
    const claimValue = `${distName}: ${address} (${statusLabel})`;

    if (
      isClaimSuppressed(
        { hcadNum: suppressKey, field: "hpo_survey", value: claimValue },
        globalRejections
      ) ||
      isClaimSuppressed(
        { hcadNum: suppressKey, field: "hpo_survey", value: address },
        globalRejections
      )
    ) {
      continue;
    }

    const surveyStyle = String(item.sy || "").trim();
    const usableStyle =
      !isReplacedStructure && isUsableHpoSurveyStyle(surveyStyle) ? surveyStyle : "";

    if (addrNormTarget) {
      const itemNorm = normalizeHpoLookupAddress(address);
      const itemDir = extractLeadingDirectional(itemNorm);
      const altNorm = item.alt ? normalizeHpoLookupAddress(item.alt) : "";
      const altDir = altNorm ? extractLeadingDirectional(altNorm) : "";
      if (targetDir && itemDir && targetDir !== itemDir && (!altDir || targetDir !== altDir)) {
        continue;
      }
      if (
        viaAddr &&
        !targetDir &&
        itemDir &&
        hasAmbiguousDirectionlessAddrCollision &&
        itemNorm !== addrNormTarget &&
        altNorm !== addrNormTarget
      ) {
        continue;
      }
    }
    const itemAddrKeys = new Set([
      ...normalizeHpoStreetAddressKeys(address),
      ...(item.alt ? normalizeHpoStreetAddressKeys(item.alt) : []),
    ]);
    const itemAddrMatch = Boolean(
      addrNormTarget &&
        (normalizeHpoLookupAddress(address) === addrNormTarget ||
          (item.alt && normalizeHpoLookupAddress(item.alt) === addrNormTarget) ||
          addrTargetKeys.some((ak) => itemAddrKeys.has(ak)))
    );
    if (isSubBuilding && (!addrNormTarget || !itemAddrMatch)) {
      continue;
    }
    const rank =
      (viaAddr ? 0 : 100) +
      (itemAddrMatch ? 40 : 0) +
      (statusCode === "C" || statusCode === "PC" ? 20 : statusCode === "NC" ? 10 : 0) +
      (surveyYear > 0 ? 5 : 0) +
      (usableStyle ? 3 : 0);

    if (rank > bestRank) {
      bestRank = rank;
      const distDocs = resolveHpoDistrictDocs(hpoIndex, distName);
      const secArr = Array.isArray(item.sec)
        ? item.sec.map((sc) => ({
            statusCode: String(sc.s || "NC").toUpperCase(),
            statusLabel: STATUS_CODE_LABELS[String(sc.s || "NC").toUpperCase()] || "Non-Contributing",
            year: String(sc.y || "").trim(),
            type: String(sc.t || "Secondary structure").trim(),
          }))
        : [];
      bestHit = {
        id: sid,
        suppressKey,
        claimValue,
        districtName: distName,
        designationDate: distDocs?.designationDate || "",
        address,
        altAddress: String(item.alt || "").trim(),
        hcadNum: String(item.h || hcad || "").trim(),
        statusCode,
        statusLabel,
        surveyYear,
        surveyYearLabel: String(item.yr || (surveyYear > 0 ? surveyYear : "")).trim(),
        surveyStyle,
        usableStyle,
        legalDesc: String(item.lg || "").trim(),
        secondaryStructures: secArr,
        inventoryPdfUrl: distDocs?.inventoryPdfUrl || "",
        guidelinesPdfUrl: distDocs?.guidelinesPdfUrl || "",
        designationReportUrl: distDocs?.designationReportUrl || "",
        mapPdfUrl: distDocs?.mapPdfUrl || "",
        isReplacedStructure,
      };
    }
  }

  return bestHit;
}

function getStatusPillModifier(statusCode) {
  if (statusCode === "C") return "status-contributing";
  if (statusCode === "PC") return "status-potentially";
  if (statusCode === "V") return "status-vacant";
  return "status-noncontributing";
}

/**
 * Build the History-tab `#inspector-hpo-survey-card` HTML string for an inspected district parcel.
 */
export function buildHpoParcelSurveyCardHtml(surveyHit) {
  if (!surveyHit || !surveyHit.districtName) return "";
  const pillMod = getStatusPillModifier(surveyHit.statusCode);
  const secondarySummary =
    Array.isArray(surveyHit.secondaryStructures) && surveyHit.secondaryStructures.length > 0
      ? surveyHit.secondaryStructures
          .map(
            (sc) =>
              `${escapeHtml(sc.type)} (${escapeHtml(sc.statusLabel)}${
                sc.year ? `, ${escapeHtml(sc.year)}` : ""
              })`
          )
          .join("; ")
      : "";
  const corePdfButtonsHtml = buildDistrictCorePdfButtonsHtml(surveyHit, {
    inventoryLabel: "&#128196; Official District Inventory (PDF) &#8599;",
    guidelinesLabel: "&#128208; District Design Guidelines (PDF) &#8599;",
    contextName: surveyHit.districtName,
    guidelinesTitleSuffix: surveyHit.districtName,
  });

  return `<div class="hpo-survey-card" id="inspector-hpo-survey-card">
    <div class="hpo-survey-header">
      <span class="hpo-survey-kicker">&#128203; COH HPO Historic District Survey</span>
      <span class="hpo-survey-status-pill ${pillMod}">${escapeHtml(surveyHit.statusLabel)}</span>
    </div>
    <div class="hpo-survey-grid">
      <div class="hpo-survey-row">
        <span class="hpo-survey-label">Historic District:</span>
        <span class="hpo-survey-val"><strong>${escapeHtml(surveyHit.districtName)}</strong></span>
      </div>
      <div class="hpo-survey-row">
        <span class="hpo-survey-label">Surveyed Address:</span>
        <span class="hpo-survey-val">${escapeHtml(surveyHit.address)}${
          surveyHit.altAddress ? ` <span class="hpo-survey-aka">(aka ${escapeHtml(surveyHit.altAddress)})</span>` : ""
        }</span>
      </div>
      ${
        surveyHit.surveyYearLabel
          ? `<div class="hpo-survey-row">
              <span class="hpo-survey-label">Circa Year Built:</span>
              <span class="hpo-survey-val mono">${escapeHtml(surveyHit.surveyYearLabel)}</span>
            </div>`
          : ""
      }
      ${
        surveyHit.usableStyle
          ? `<div class="hpo-survey-row">
              <span class="hpo-survey-label">Surveyed Architectural Style:</span>
              <span class="hpo-survey-val">
                <button
                  type="button"
                  class="inspector-filter-chip"
                  data-filter-chip="${escapeHtml(surveyHit.usableStyle)}"
                  data-filter-label="Architectural Style: ${escapeHtml(surveyHit.usableStyle)}"
                  title="Filter map to structures sharing style '${escapeHtml(surveyHit.usableStyle)}'"
                >${escapeHtml(surveyHit.usableStyle)} &#128269;</button>
              </span>
            </div>`
          : surveyHit.surveyStyle
          ? `<div class="hpo-survey-row">
              <span class="hpo-survey-label">Survey Classification Note:</span>
              <span class="hpo-survey-val">${escapeHtml(surveyHit.surveyStyle)}</span>
            </div>`
          : ""
      }
      ${
        surveyHit.legalDesc
          ? `<div class="hpo-survey-row">
              <span class="hpo-survey-label">Legal / Lot:</span>
              <span class="hpo-survey-val mono">${escapeHtml(surveyHit.legalDesc)}</span>
            </div>`
          : ""
      }
      ${
        secondarySummary
          ? `<div class="hpo-survey-row">
              <span class="hpo-survey-label">Secondary Structures:</span>
              <span class="hpo-survey-val">${secondarySummary}</span>
            </div>`
          : ""
      }
    </div>
    ${
      surveyHit.isReplacedStructure
        ? `<div class="hpo-survey-note">Note: Current HCAD appraisal records indicate a post-survey structure on this parcel; official HPO classification reflects the district inventory on file.</div>`
        : ""
    }
    <div class="hpo-survey-actions">
      ${corePdfButtonsHtml}
      <button
        type="button"
        class="quick-flag-btn"
        data-quick-flag="claim"
        data-flag-hcad="${escapeHtml(surveyHit.suppressKey || "")}"
        data-flag-field="hpo_survey"
        data-flag-value="${escapeHtml(surveyHit.claimValue || surveyHit.address)}"
        data-flag-provenance="${escapeHtml(
          `COH HPO Official Inventory: ${surveyHit.districtName}`
        )}"
        title="Flag an incorrect HPO parcel survey match; hides locally and queues for Preservation Houston review"
      >&#9873; Flag Match</button>
    </div>
  </div>`;
}

/**
 * Build the District Boundary Dossier `#boundary-hpo-district-docs-card` HTML string.
 */
export function buildHpoDistrictDossierCardHtml(districtName, distDocs) {
  if (!distDocs) return "";
  const counts = distDocs.statusCounts || {};
  const cCount = Number(counts.Contributing) || 0;
  const pcCount = Number(counts["Potentially Contributing"]) || 0;
  const ncCount = Number(counts["Non-Contributing"]) || 0;
  const vCount = Number(counts.Vacant) || 0;
  const totalCount = Number(distDocs.surveyedParcelCount) || cCount + pcCount + ncCount + vCount;
  const corePdfButtonsHtml = buildDistrictCorePdfButtonsHtml(distDocs, {
    inventoryLabel: "&#128203; District Inventory (PDF) &#8599;",
    guidelinesLabel: "&#128208; Design Guidelines (PDF) &#8599;",
    contextName: districtName || distDocs.districtName,
  });

  return `<div class="hpo-district-dossier-card" id="boundary-hpo-district-docs-card">
    <div class="hpo-survey-header">
      <span class="hpo-survey-kicker">&#127963; Official COH HPO Survey &amp; Design Standards</span>
      ${
        distDocs.designationDate
          ? `<span class="hpo-district-date-badge">Designated ${escapeHtml(
              distDocs.designationDate
            )}</span>`
          : ""
      }
    </div>
    ${
      totalCount > 0
        ? `<div class="hpo-district-counts-bar">
            <span class="hpo-district-count-chip total"><strong>${totalCount.toLocaleString()}</strong> Surveyed Parcels</span>
            ${
              cCount > 0
                ? `<span class="hpo-district-count-chip contributing"><strong>${cCount.toLocaleString()}</strong> Contributing</span>`
                : ""
            }
            ${
              pcCount > 0
                ? `<span class="hpo-district-count-chip potentially"><strong>${pcCount.toLocaleString()}</strong> Potentially Contributing</span>`
                : ""
            }
            ${
              ncCount > 0
                ? `<span class="hpo-district-count-chip"><strong>${ncCount.toLocaleString()}</strong> Non-Contributing</span>`
                : ""
            }
            ${
              vCount > 0
                ? `<span class="hpo-district-count-chip vacant"><strong>${vCount.toLocaleString()}</strong> Vacant</span>`
                : ""
            }
          </div>`
        : ""
    }
    <div class="hpo-survey-actions">
      ${corePdfButtonsHtml}
    </div>
  </div>`;
}

const _landmarkOverlayIndexCache = new WeakMap();

function getLandmarkOverlayIndex(overlaysData) {
  if (!overlaysData || typeof overlaysData !== "object") return null;
  const cached = _landmarkOverlayIndexCache.get(overlaysData);
  if (cached) return cached;

  const byName = new Map();
  const byRawAddr = new Map();
  const byNormAddr = new Map();
  const byHcad = new Map();
  const relocatedByHcad = new Map();

  const pushBucket = (map, key, val) => {
    if (!key) return;
    let arr = map.get(key);
    if (!arr) {
      arr = [];
      map.set(key, arr);
    }
    arr.push(val);
  };

  const lmFeatures = overlaysData.landmarks?.features;
  if (Array.isArray(lmFeatures)) {
    for (let i = 0; i < lmFeatures.length; i += 1) {
      const lp = lmFeatures[i]?.properties || {};
      const lHcad = String(lp.hcad_num || "").trim();
      const lName = String(lp.name || "").trim().toLowerCase();
      const lAddr = String(lp.address || "").trim().toLowerCase();
      const lAddrKeys = !lp.demolished ? normalizeHpoStreetAddressKeys(lp.address) : [];
      const lAddrKey = lAddrKeys[0] || "";
      const entry = { order: i, lp, lHcad, lName, lAddr, lAddrKey, lAddrKeys };
      pushBucket(byName, lName, entry);
      if (!lp.demolished) {
        pushBucket(byRawAddr, lAddr, entry);
        for (const ak of lAddrKeys) {
          pushBucket(byNormAddr, ak, entry);
        }
      }
      pushBucket(byHcad, lHcad, entry);
    }
  }

  const relocFeatures = overlaysData.relocated_structures?.features;
  if (Array.isArray(relocFeatures)) {
    for (const rf of relocFeatures) {
      const rp = rf?.properties || {};
      const rHcad = String(rp.hcad_num || "").trim();
      if (rHcad && !relocatedByHcad.has(rHcad)) {
        relocatedByHcad.set(rHcad, rp);
      }
    }
  }

  const idx = { byName, byRawAddr, byNormAddr, byHcad, relocatedByHcad };
  _landmarkOverlayIndexCache.set(overlaysData, idx);
  return idx;
}

/**
 * Resolve City of Houston Landmark Designation Report and relocated-structure metadata using a
 * `WeakMap`-cached index on `overlaysData`, without mutating `props` or leaking across `#<slug>`
 * sub-buildings, post-1965 replacement structures, or multi-landmark HCAD parcels.
 */
export function resolveCohLandmarkOverlayMetadata(overlaysData, props = {}, title = "") {
  const rawInspectId = String(props.building_id || props.id || "").trim();
  const isLostInspect =
    Boolean(props.is_lost_houston) ||
    rawInspectId.startsWith("lost_") ||
    String(props.type || "") === "lost_houston";
  const isSubBuilding = rawInspectId.replace(/#(?:fp|aux)_\d+$/, "").includes("#");
  const hcadNum = String(props.hcad_num || "").trim();
  const buildingYear = Number(props.year_built) || 0;

  let reportPdfUrl = String(props.landmark_report_url || props.report_pdf_url || "");
  let secondaryPdfUrl = String(props.secondary_pdf_url || "");
  let landmarkCode = String(props.landmark_code || props.plm_num || props.lm_num || "");
  let landmarkSummary = String(props.landmark_summary || props.pdf_summary || "");
  let builder = String(props.builder || "");
  let builderSource = String(props.builder_source || "");
  let originalPermit = String(props.original_permit || "");
  let originalCost = String(props.original_cost || "");
  let engineers = String(props.engineers || "");
  let wasRelocated = Boolean(props.was_relocated);
  let relocationNote = String(props.relocation_note || "");
  let histContract = props.hist_contract || null;

  const idx = getLandmarkOverlayIndex(overlaysData);
  const needsLandmarkHydration =
    !reportPdfUrl ||
    !secondaryPdfUrl ||
    !landmarkCode ||
    !landmarkSummary ||
    !builder ||
    !builderSource ||
    !originalPermit ||
    !originalCost ||
    !engineers;
  if (needsLandmarkHydration && idx) {
    const normTitle = String(title || "").trim().toLowerCase();
    const normAddr = String(props.address || "").trim().toLowerCase();
    const normAddrKeys = normalizeHpoStreetAddressKeys(props.address);
    const normAddrKey = normAddrKeys[0] || "";
    const targetDir = normAddrKey ? extractLeadingDirectional(normAddrKey) : "";

    const seenOrders = new Set();
    const candidates = [];
    const addBucket = (arr) => {
      if (!arr) return;
      for (const item of arr) {
        if (!seenOrders.has(item.order)) {
          seenOrders.add(item.order);
          candidates.push(item);
        }
      }
    };
    if (normTitle) addBucket(idx.byName.get(normTitle));
    if (!isSubBuilding && normAddr) addBucket(idx.byRawAddr.get(normAddr));
    if (!isSubBuilding) {
      for (const ak of normAddrKeys) {
        addBucket(idx.byNormAddr.get(ak));
      }
    }
    const hcadBucket = !isSubBuilding && hcadNum ? idx.byHcad.get(hcadNum) : null;
    if (hcadBucket && hcadBucket.length === 1) {
      addBucket(hcadBucket);
    }
    if (candidates.length > 1) {
      candidates.sort((a, b) => a.order - b.order);
    }

    for (const cand of candidates) {
      const { lp, lHcad, lName, lAddr, lAddrKey, lAddrKeys } = cand;
      if (reportPdfUrl && lp.report_pdf_url && reportPdfUrl !== lp.report_pdf_url) {
        continue;
      }
      const titleMatched = Boolean(normTitle && lName && normTitle === lName);
      const lmYear = Number(lp.year_built) || 0;
      const isReplacedStructure = !titleMatched && isPostLandmarkReplacement(buildingYear, lmYear);
      if (isReplacedStructure) {
        continue;
      }
      const candDir = lAddrKey ? extractLeadingDirectional(lAddrKey) : "";
      const opposingDir = Boolean(targetDir && candDir && targetDir !== candDir);
      const mismatchedDir = Boolean((targetDir || candDir) && targetDir !== candDir);
      const conflictingKnownHcad = Boolean(
        hcadNum && lHcad && hcadNum.length === 13 && lHcad.length === 13 && hcadNum !== lHcad
      );
      const hasNormKeyOverlap = Boolean(
        normAddrKeys.length > 0 &&
          ((lAddrKey && normAddrKeys.includes(lAddrKey)) ||
            (Array.isArray(lAddrKeys) && normAddrKeys.some((ak) => lAddrKeys.includes(ak))))
      );
      const addrMatched = Boolean(
        !isSubBuilding &&
          !lp.demolished &&
          !mismatchedDir &&
          !conflictingKnownHcad &&
          ((normAddr && lAddr && normAddr === lAddr) || hasNormKeyOverlap)
      );
      const singleHcadMatched = Boolean(
        !isSubBuilding &&
          (!lp.demolished || isLostInspect) &&
          hcadNum &&
          lHcad === hcadNum &&
          hcadBucket &&
          hcadBucket.length === 1 &&
          !opposingDir &&
          (!normAddrKey || !lAddrKey || hasNormKeyOverlap)
      );
      if (titleMatched || addrMatched || singleHcadMatched) {
        if (!reportPdfUrl && lp.report_pdf_url) reportPdfUrl = String(lp.report_pdf_url);
        if (!secondaryPdfUrl && lp.secondary_pdf_url) secondaryPdfUrl = String(lp.secondary_pdf_url);
        if (!landmarkCode) landmarkCode = String(lp.plm_num || lp.lm_num || "");
        if (!landmarkSummary) landmarkSummary = String(lp.pdf_summary || "");
        if (!builder && lp.builder) builder = String(lp.builder);
        if (!builderSource && lp.builder_source) builderSource = String(lp.builder_source);
        if (!originalPermit && lp.original_permit) originalPermit = String(lp.original_permit);
        if (!originalCost && lp.original_cost) originalCost = String(lp.original_cost);
        if (!engineers && lp.engineers) engineers = String(lp.engineers);
        break;
      }
    }
  }

  const relocRec = hcadNum && !isSubBuilding ? idx?.relocatedByHcad.get(hcadNum) : null;
  const relocYear = Number(relocRec?.year_built) || 0;
  const isPostRelocReplacement = isPostLandmarkReplacement(buildingYear, relocYear);
  if (relocRec && !isPostRelocReplacement) {
    if (!builder && relocRec.builder) builder = String(relocRec.builder);
    if (!builderSource && relocRec.builder_source) builderSource = String(relocRec.builder_source);
    if (!wasRelocated && relocRec.was_relocated) wasRelocated = true;
    if (!relocationNote && relocRec.relocation_note) {
      relocationNote = String(relocRec.relocation_note);
    }
    if (!histContract && relocRec.hist_contract) histContract = relocRec.hist_contract;
    if (!originalCost && relocRec.original_cost) originalCost = String(relocRec.original_cost);
    if (!originalPermit && relocRec.original_permit) {
      originalPermit = String(relocRec.original_permit);
    }
  }

  return {
    reportPdfUrl: sanitizeHttpUrl(reportPdfUrl),
    secondaryPdfUrl: sanitizeHttpUrl(secondaryPdfUrl),
    landmarkCode,
    landmarkSummary,
    builder,
    builderSource,
    originalPermit,
    originalCost,
    engineers,
    wasRelocated,
    relocationNote,
    histContract,
  };
}

/**
 * Build the HTML-escaped `.coh-landmark-report-card` block (including NRHP secondary PDF fallback
 * and unpublished TPIA public-records notice).
 */
export function buildCohLandmarkDossierCardHtml({
  landmarkCode = "",
  reportPdfUrl = "",
  secondaryPdfUrl = "",
  landmarkSummary = "",
  isCuratedOverride = false,
  sourceCitation = "",
} = {}) {
  const rawReportUrl = sanitizeHttpUrl(reportPdfUrl);
  const rawSecondaryUrl = sanitizeHttpUrl(secondaryPdfUrl);
  const rawCode = String(landmarkCode || "").trim();
  const rawSummary = String(landmarkSummary || "").trim();
  if (!rawReportUrl && !rawSecondaryUrl && !rawCode) return "";

  const safeLandmarkCode = escapeHtml(rawCode);
  const safeReportPdfUrl = escapeHtml(rawReportUrl);
  const safeSecondaryPdfUrl = escapeHtml(rawSecondaryUrl);
  const safeLandmarkSummary = escapeHtml(rawSummary);
  const isSecondaryNrhpPdf = rawSecondaryUrl.includes("atlas.thc.texas.gov");
  const secondaryPdfLabel = isSecondaryNrhpPdf
    ? "Open National Register Nomination (PDF) &#8599;"
    : "Supplemental HAHC Filing / Action Report (PDF) &#8599;";
  // Suppress duplicate summary prose when `curated_overrides.source_citation` already embeds
  // the same landmark summary prefix (`"{cite_code}: {landmark_summary}"[:360]` from Python harvest).
  const showSummary = Boolean(
    safeLandmarkSummary &&
      (!isCuratedOverride || !String(sourceCitation || "").includes(rawSummary.slice(0, 40)))
  );

  return `<div class="coh-landmark-report-card">
    <div class="coh-landmark-report-header">
      <span class="coh-landmark-report-kicker">&#127963; COH Landmark Designation Dossier</span>
      ${
        safeLandmarkCode
          ? `<span class="coh-landmark-code-badge" title="City of Houston Historic Preservation Office File #">HPO #${safeLandmarkCode}</span>`
          : ""
      }
    </div>
    ${showSummary ? `<div class="coh-landmark-report-summary">${safeLandmarkSummary}</div>` : ""}
    ${
      safeReportPdfUrl || safeSecondaryPdfUrl
        ? `<div class="coh-landmark-report-actions">
            ${
              safeReportPdfUrl
                ? `<a
                    href="${safeReportPdfUrl}"
                    target="_blank"
                    rel="noopener noreferrer"
                    class="coh-landmark-pdf-btn"
                    title="Open the official City of Houston Archaeological &amp; Historical Commission (HAHC) Landmark Designation Report PDF"
                  >
                    &#128196; Open Official Landmark Report (PDF) &#8599;
                  </a>`
                : ""
            }
            ${
              safeSecondaryPdfUrl && rawSecondaryUrl !== rawReportUrl
                ? `<a
                    href="${safeSecondaryPdfUrl}"
                    target="_blank"
                    rel="noopener noreferrer"
                    class="coh-landmark-pdf-btn ${safeReportPdfUrl ? "secondary" : ""}"
                  >
                    ${safeReportPdfUrl ? "" : "&#128196; "}${secondaryPdfLabel}
                  </a>`
                : ""
            }
          </div>`
        : ""
    }
    ${
      !safeReportPdfUrl && safeLandmarkCode
        ? `<div class="coh-landmark-tpia-note">Standalone City of Houston HPO designation report PDF (HPO #${safeLandmarkCode}) is unpublished on houstontx.gov and queued for public records release.</div>`
        : ""
    }
  </div>`;
}
