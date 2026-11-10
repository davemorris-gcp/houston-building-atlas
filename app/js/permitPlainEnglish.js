/**
 * Plain-English decoder and XSS-safe HTML renderer for HCAD appraiser notes,
 * municipal building permits, and extra-feature records in `#inspector-permits-card`.
 *
 * Design principles:
 * 1. Fidelity first: verbatim public-record text is always preserved (via a 1-tap
 *    `<details>` disclosure on cryptic HCAD appraiser notes, and as the primary row
 *    line on municipal permits and dated extra features).
 * 2. Zero noise when already clear: notes and permits without technical shorthand
 *    render with zero extra chrome or redundant explanations.
 * 3. Pure ES module with zero DOM globals so Node.js unit tests and browser UI share
 *    100% identical logic.
 */

export const VALID_PERMIT_CAT_CODES = new Set([
  "new",
  "remodel",
  "add",
  "demo",
  "asbestos",
  "reloc",
  "aux",
  "roof",
  "mep",
  "repair",
  "other",
]);

const MONTH_ABBR = [
  "",
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

/**
 * Escape arbitrary public-record text for safe HTML interpolation.
 * @param {unknown} value
 * @returns {string}
 */
export function escapePermitHtml(value) {
  const s = String(value ?? "");
  if (!s) return "";
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/**
 * Normalize `p.src` values (`"tx_dshs"`, `"TX_DSHS"`, `"coh_live"`, `"coh_weekly"`)
 * into a human-readable source label.
 * @param {unknown} src
 * @returns {string}
 */
export function normalizePermitSourceLabel(src) {
  const norm = String(src ?? "")
    .trim()
    .toUpperCase();
  if (!norm) return "";
  if (norm === "TX_DSHS" || norm === "DSHS") return "TX DSHS Notification";
  if (norm === "COH_LIVE" || norm === "COH_WEEKLY" || norm === "COH") {
    return "COH Live Permit";
  }
  return "";
}

const INSPECTION_PREFIX_LABELS = new Map([
  ["NC", "Permit check"],
  ["N/C", "Permit check"],
  ["FR", "Field review"],
  ["F/R", "Field review"],
  ["FC", "Field check"],
  ["F/C", "Field check"],
  ["OR", "Office review"],
  ["O/R", "Office review"],
  ["OS", "Aerial / sketch review"],
  ["O/S", "Aerial / sketch review"],
  ["SV", "Sales check"],
  ["S/V", "Sales check"],
  ["SALE", "Sales check"],
  ["FH", "Formal protest hearing"],
  ["FRML", "Formal protest hearing"],
  ["FORMAL", "Formal protest hearing"],
  ["IH", "Informal protest hearing"],
  ["INF", "Informal protest hearing"],
  ["INFORMAL", "Informal protest hearing"],
  ["ARB", "Appraisal Review Board"],
  ["REIN", "Re-inspection"],
  ["REINSP", "Re-inspection"],
  ["REINSPN", "Re-inspection"],
  ["AR", "Appraisal review"],
  ["A/R", "Appraisal review"],
  ["DR", "Disaster review"],
  ["MAINT", "Maintenance review"],
]);

const escapeRegExp = (s) => String(s).replace(/[.*+?^${}()|[\]\\/]/g, "\\$&");

const INSPECTION_CODE_ALT = `(?:${Array.from(INSPECTION_PREFIX_LABELS.keys())
  .sort((a, b) => b.length - a.length)
  .map(escapeRegExp)
  .join("|")})`;

const SUBAREA_CODE_LABELS = {
  BAS: "ground-floor living area",
  BAU: "upper-story living area",
  BAL: "lower-level living area",
  OFP: "open frame porch",
  OFU: "upper open porch",
  OFL: "lower open porch",
  EFP: "enclosed frame porch",
  OMP: "open masonry porch",
  OMU: "upper masonry porch",
  FSP: "1-story frame addition",
  FSU: "upper-story frame addition",
  MSP: "masonry addition",
  MSU: "upper masonry addition",
  FGP: "attached frame garage",
  FGL: "lower frame garage",
  MGP: "attached masonry garage",
  MGL: "lower masonry garage",
  CRP: "carport",
  RC1: "detached carport",
  CPP: "canopy",
  CPL: "canopy",
  CP5: "canopy",
  CP6: "canopy",
  MPP: "masonry patio",
  MPL: "masonry patio",
  WDP: "wood deck",
  WDU: "elevated wood deck",
  SPP: "stone/tile patio",
  MTP: "masonry terrace",
  FUP: "frame utility shed",
  FUL: "frame utility room",
  ATF: "finished attic",
  ATU: "unfinished attic",
};

const SUBAREA_CODE_ALT = Object.keys(SUBAREA_CODE_LABELS)
  .sort((a, b) => b.length - a.length)
  .map(escapeRegExp)
  .join("|");

const GRADE_CODE_LABELS = {
  EX: "Excellent",
  EXC: "Excellent",
  VG: "Very Good",
  VGOOD: "Very Good",
  GD: "Good",
  AV: "Average",
  AVE: "Average",
  AVG: "Average",
  FR: "Fair",
  FAIR: "Fair",
  PR: "Poor",
  POOR: "Poor",
  VP: "Very Poor",
  VPOOR: "Very Poor",
  UN: "Unsound",
  US: "Unsound",
};

const GRADE_TOKEN_ALT = `(?:${Object.keys(GRADE_CODE_LABELS)
  .sort((a, b) => b.length - a.length)
  .map(escapeRegExp)
  .join("|")})`;

const NON_STREET_GRADE_ALT =
  "(?:EX|EXC|VG|VGOOD|GD|AV|AVG|FR|FAIR|PR|POOR|VP|VPOOR|UN)";

const ADJ_TO_GRADE_RE = new RegExp(
  `\\b(ADJ(?:USTED)?\\b[^.;]{0,24}?\\bTO)\\s+(${NON_STREET_GRADE_ALT})\\b`,
  "gi"
);

const SHORTHAND_DETECTION_RE = new RegExp(
  [
    `(?:^|\\s)\\b${INSPECTION_CODE_ALT}\\s*[:\\-]`,
    `^\\s*(?:\\d{1,2}\\/\\d{1,2}\\/\\d{2,4}|\\d{1,2}\\/\\d{4}|\\d{1,2}\\/\\d{2})(?:\\s*[-:]\\s*|\\s+)${INSPECTION_CODE_ALT}\\b`,
    "\\bSTI\\s+HAD\\b",
    "\\bTOI\\b",
    "\\bTOS\\b",
    "\\bCDU(?:\\/[A-Z]+)?\\b",
    "\\bPCR\\b",
    "\\bDWGS?\\b",
    "\\bIMPS\\b",
    "\\bIMPR\\b",
    "\\bP\\/U\\b",
    "\\bN\\/PU\\b",
    "\\bN\\/T\\b",
    "\\bN\\/V\\b",
    "\\bN\\/C\\b",
    "\\bP\\/O\\b",
    "\\bPER\\s+PO\\b",
    "\\bPO\\s+(?:PHOTOS?|PICS?|DOCS?|INFO|STATED|SAID|DID|SUBMITTED|PROVIDED|NOT)\\b",
    "\\bPER\\s*MLS\\b",
    "\\bRVST\\b",
    "\\bL\\/A\\b",
    "\\bU\\/M\\b",
    `\\b(?:${SUBAREA_CODE_ALT})\\s*(?:\\(\\d+\\)|\\b)`,
    "\\b(?:ADDN|ADDTNS?|ADDT|RMDL|REMOD|RPRS|REPS|FND|SHTROCK|SHTRK|WNDWS|BDRMS?|MSTR|OB&Y|XFEAT|(?:DET|ATT)\\s+GAR)\\b",
    `@(?!\\s*(?:AVE\\s+(?:[A-Z0-9]\\b|OF\\b)|(?:US|PR)(?:\\s*-\\s*|\\s+)\\d))\\s*${GRADE_TOKEN_ALT}\\b`,
    `\\bFROM\\s+${GRADE_TOKEN_ALT}\\s+TO\\s+${GRADE_TOKEN_ALT}\\b`,
    ADJ_TO_GRADE_RE.source,
    "\\bW\\/(?:O|H|D)\\b",
  ].join("|"),
  "i"
);

const DATE_CANDIDATE_RE =
  /\b(\d{1,2}\/\d{1,2}\/\d{2,4}|\d{1,2}\/\d{4}|\d{1,2}\/\d{2})\b/g;

const PREPOSITION_BEFORE_DATE_RE =
  /\b(?:AS\s+OF|PRIOR\s+TO|BY|BEFORE|AFTER|SINCE|DEMOED|DEMO'D|DEMOLISHED|PER|AT|ON|IN|FROM|TO|THRU|THROUGH|UNTIL|COMPLETED|COMP|DONE|BUILT|ERECTED|ISSUED|FILED|SOLD|EFF|EFFECTIVE)\s*$/i;

const AFTER_DATE_INSPECTION_RE = new RegExp(
  `^\\s*[-:]?\\s*${INSPECTION_CODE_ALT}(?:\\s*[:\\-]|\\s+[A-Z0-9#@]|\\s*$)`,
  "i"
);

/**
 * Format an HCAD inspection date token without guessing ambiguous 2-part `M/DD` vs `M/YY`.
 * @param {string} rawDate
 * @param {boolean} followedByInspectionCode
 * @returns {string}
 */
export function formatHcadInspectionDate(rawDate, followedByInspectionCode = false) {
  const token = String(rawDate || "").trim();
  const parts = token.split("/");
  if (parts.length === 3) {
    const m = Number.parseInt(parts[0], 10);
    const d = Number.parseInt(parts[1], 10);
    let y = Number.parseInt(parts[2], 10);
    if (m >= 1 && m <= 12 && d >= 1 && d <= 31 && Number.isFinite(y)) {
      if (parts[2].length <= 2) {
        y = y <= 35 ? 2000 + y : 1900 + y;
      }
      if (y >= 1900 && y <= 2099) {
        return `${MONTH_ABBR[m]} ${d}, ${y}`;
      }
    }
    return token;
  }
  if (parts.length === 2) {
    const m = Number.parseInt(parts[0], 10);
    const second = Number.parseInt(parts[1], 10);
    if (m >= 1 && m <= 12 && parts[1].length === 4 && second >= 1900 && second <= 2099) {
      return `${MONTH_ABBR[m]} ${second}`;
    }
    if (
      followedByInspectionCode &&
      m >= 1 &&
      m <= 12 &&
      parts[1].length === 2 &&
      second >= 0 &&
      second <= 35
    ) {
      return `${MONTH_ABBR[m]} ${2000 + second}`;
    }
  }
  return token;
}

function formatCommaInt(numStr) {
  const n = Number.parseInt(numStr, 10);
  if (!Number.isFinite(n)) return numStr;
  return n.toLocaleString("en-US");
}

const UNDATED_CLAUSE_SPLIT_RE = new RegExp(
  `(?:;|\\/\\/)\\s*(?=${INSPECTION_CODE_ALT}\\s*[:\\-])`,
  "gi"
);

const SUBAREA_PAREN_RE = new RegExp(
  `\\b(${SUBAREA_CODE_ALT})\\s*\\((\\d{1,7})\\)`,
  "gi"
);

const SUBAREA_VERB_RE = new RegExp(
  `\\b(P\\/U|PU|ADD|ADDED|NEW|DEMO|DEMOED|REMOVED)\\s+(${SUBAREA_CODE_ALT})\\b`,
  "gi"
);

const GRADE_FROM_TO_RE = new RegExp(
  `\\b(FROM)\\s+(${GRADE_TOKEN_ALT})\\s+(TO)\\s+(${GRADE_TOKEN_ALT})\\b`,
  "gi"
);

const GRADE_AT_RE = new RegExp(
  `((?:physical\\s+)?condition rating(?:\\s*(?:@|at|is|kept\\s*@|kept\\s+at|adj\\s+to|to|:|=))?|kept\\s+@|@)\\s*(${GRADE_TOKEN_ALT})\\b`,
  "gi"
);

const GRADE_REV_FROM_RE = new RegExp(
  `((?:physical\\s+)?condition rating\\s+to\\s+[A-Za-z ]+\\s+from)\\s+(${GRADE_TOKEN_ALT})\\b`,
  "gi"
);

const PRESERVE_CASE_RULES = Object.freeze([
  ...[
    "HCAD",
    "COH",
    "MLS",
    "ARB",
    "HVAC",
    "AC",
    "A/C",
    "TX",
    "PO",
    "II",
    "III",
    "IV",
    "NW",
    "NE",
    "SW",
    "SE",
    "LLC",
    "INC",
    "ISD",
  ].map((tok) => [new RegExp(`\\b${escapeRegExp(tok)}\\b`, "gi"), tok]),
  ...[
    ["EXCELLENT", "Excellent"],
    ["VERY GOOD", "Very Good"],
    ["GOOD", "Good"],
    ["AVERAGE", "Average"],
    ["FAIR", "Fair"],
    ["POOR", "Poor"],
    ["VERY POOR", "Very Poor"],
    ["UNSOUND", "Unsound"],
  ].map(([k, v]) => [new RegExp(`\\b${escapeRegExp(k)}\\b`, "gi"), v]),
]);

const WORD_REPLACEMENTS = Object.freeze([
  [/\bDWGS\b/gi, "dwellings"],
  [/\bDWG\b/gi, "main dwelling"],
  [/\bIMPS\b/gi, "improvements"],
  [/\bIMPR\b/gi, "improvements"],
  [/\bXFEAT\b/gi, "extra feature"],
  [/\bOB&Y\b/gi, "outbuilding/yard feature"],
  [/\bL\/A\b/gi, "living area"],
  [/\bU\/M\s+SINKS?\b/gi, "undermount sinks"],
  [/\bADJ\b/gi, "adjusted"],
  [/\bRMDL\b/gi, "remodeled"],
  [/\bREMOD\b/gi, "remodeled"],
  [/\b(?:ADDN|ADDTN|ADDT)\b/gi, "addition"],
  [/\bADDTNS\b/gi, "additions"],
  [/\bRPRS\b/gi, "repairs"],
  [/\bREPS\b/gi, "repairs"],
  [/\bFND\b/gi, "foundation"],
  [/\bKIT\b/gi, "kitchen"],
  [/\bBTHS\b/gi, "baths"],
  [/\bBTH\b/gi, "bath"],
  [/\bBDRMS?\b/gi, "bedrooms"],
  [/\bMSTR\b/gi, "primary"],
  [/\bSHTROCK\b|\bSHTRK\b/gi, "sheetrock/drywall"],
  [/\bWNDWS\b/gi, "windows"],
  [/\bFLRS\b/gi, "floors"],
  [/\bFLR\b/gi, "floor"],
  [/\bCLG\b/gi, "ceiling"],
  [/\bDET\s+GAR\b/gi, "detached garage"],
  [/\bATT\s+GAR\b/gi, "attached garage"],
  [/\bGAR\b/gi, "garage"],
  [/\bCARP\b/gi, "carport"],
  [/\bBLDGS\b/gi, "buildings"],
  [/\bBLDG\b/gi, "building"],
  [/\bPICS\b/gi, "photos"],
  [/\bHRG\b/gi, "hearing"],
  [/\bVAL\b/gi, "appraised value"],
  [/\bEST\b/gi, "estimate"],
  [/\bCOMP\b|\bCOMPL\b|\bCMPLT\b/gi, "complete"],
  [/\bDEMOED\b|\bDEMO'D\b/gi, "demolished"],
  [/\bW\/O\b/gi, "without"],
  [/\bW\/H\b/gi, "water heater"],
  [/\bW\/D\b/gi, "washer/dryer"],
  [/\bW\/(?=[A-Za-z]{2})/gi, "with "],
  [/\bSQFT\b|\bSQ\/FT\b|\bS\/F\b/gi, "sq ft"],
]);

const WHITESPACE_COLLAPSE_RE = /\s+/g;
const PREV_CLAUSE_END_TOKEN_RE =
  /(?:\bTOI|\bGD|\bAVG|\bFR|\bPR|\bVG|\bEX|\bCOMP|%|\))\s*$/i;
const SEGMENT_DELIM_TRIM_RE = /^(?:[;\s]|\/\/)+|(?:[;\s]|\/\/)+$/g;
const TRAILING_SLASHES_RE = /\s*\/+\s*$/;
const SPACE_BEFORE_PUNCT_RE = /\s+([,.;:])/g;
const PO_BOX_LOWER_RE = /\bpo box\b/gi;
const HIGHWAY_ROUTE_PREFIX_RE = /\b(us|pr)(?=(?:\s*-\s*|\s+)\d)/gi;
const LEADING_ALPHA_RE = /^([^a-zA-Z0-9]*)([a-z])/;
const TERMINAL_PUNCT_RE = /[.!?)]$/;
const PO_BOX_PROTECT_RE = /\bP\.?\s*O\.?\s+BOX\b/gi;
const PO_BOX_RESTORE_RE = /__PO_BOX__/g;
const CONDITION_RATING_TEST_RE = /condition rating/i;
const AVE_STREET_SUFFIX_RE = /^\s+(?:[A-Z0-9]\b|OF\b)/i;
const ROUTE_NUM_SUFFIX_RE = /^(?:\s*-\s*|\s+)\d/;
const AT_SIGN_GLOBAL_RE = /@/g;
const AT_PERCENT_RE = /@\s*(\d{1,3}%)/g;
const PO_NEXT_WORD_RE =
  /\bPO\s+(PHOTOS?|PICS?|DOCS?|INFO|STATED|SAID|DID|SUBMITTED|PROVIDED|NOT)\b/gi;
const STI_HAD_PREFIX_RE = /^STI\s+HAD\s*:\s*(.+)$/i;

const CDU_REPLACEMENTS = Object.freeze([
  [/\bCDU\/(?:PCR|PHYS?|PC|COND)\b/gi, "physical condition rating"],
  [/\bCDU\b/gi, "condition rating"],
  [/\bPCR\b/gi, "physical condition rating"],
]);

const PHRASE_REPLACEMENTS = Object.freeze([
  [/@\s*TOI\b/gi, "at time of inspection"],
  [/\bAT\s+TOI\b/gi, "at time of inspection"],
  [/\bPER\s+TOI\b/gi, "at time of inspection"],
  [/\bTOI\b/gi, "at time of inspection"],
  [/@\s*TOS\b/gi, "at time of sale"],
  [/\bAT\s+TOS\b/gi, "at time of sale"],
  [/\bAFTER\s+TOS\b/gi, "after time of sale"],
  [/\bPRIOR\s+TO\s+TOS\b/gi, "before time of sale"],
  [/\bTOS\b/gi, "time of sale"],
  [/\bN\/PU\b/gi, "not added to tax roll"],
  [/\bP\/U\b/gi, "added to tax roll"],
  [/\bN\/T\b/gi, "non-taxable"],
  [/\bN\/V\s+FROM\s+(?:ST|STR|STREET|RD)\b/gi, "not visible from street"],
  [/\bN\/V\b/gi, "not visible"],
  [/\bN\/C\b/gi, "no change"],
  [/\bRVST\b/gi, "flagged for follow-up revisit"],
  [/\bPERMLS\b/gi, "per real-estate listing (MLS)"],
  [/\bPER\s+MLS\b/gi, "per real-estate listing (MLS)"],
  [/\bP\/O\b/gi, "property owner"],
  [/\bPER\s+PO\b/gi, "per property owner"],
]);

function splitAppraiserSegments(raw) {
  const text = raw.replace(WHITESPACE_COLLAPSE_RE, " ").trim();
  const splitIndices = [0];

  for (const match of text.matchAll(DATE_CANDIDATE_RE)) {
    const idx = match.index;
    if (idx === 0) continue;
    const before = text.slice(0, idx);
    const after = text.slice(idx + match[0].length);
    const hasInspPrefix = AFTER_DATE_INSPECTION_RE.test(after);
    const precededByPrep = PREPOSITION_BEFORE_DATE_RE.test(before);
    if (precededByPrep && !hasInspPrefix) {
      continue;
    }
    const prevChar = text[idx - 1];
    const prevTrim = before.trimEnd();
    const prevEndChar = prevTrim ? prevTrim[prevTrim.length - 1] : "";
    if (
      hasInspPrefix ||
      prevEndChar === ";" ||
      prevEndChar === "." ||
      (prevChar === " " && PREV_CLAUSE_END_TOKEN_RE.test(before))
    ) {
      splitIndices.push(idx);
    }
  }

  for (const match of text.matchAll(UNDATED_CLAUSE_SPLIT_RE)) {
    const nextIdx = match.index + match[0].length;
    if (nextIdx > 0 && !splitIndices.includes(nextIdx)) {
      splitIndices.push(nextIdx);
    }
  }
  splitIndices.sort((a, b) => a - b);

  const rawChunks = [];
  for (let i = 0; i < splitIndices.length; i += 1) {
    const start = splitIndices[i];
    const end = i + 1 < splitIndices.length ? splitIndices[i + 1] : text.length;
    const slice = text
      .slice(start, end)
      .replace(SEGMENT_DELIM_TRIM_RE, "")
      .trim();
    if (slice) rawChunks.push(slice);
  }
  return rawChunks.length > 0 ? rawChunks : [text];
}

function sentenceCaseClause(text) {
  const s = String(text || "")
    .replace(WHITESPACE_COLLAPSE_RE, " ")
    .replace(TRAILING_SLASHES_RE, "")
    .replace(SPACE_BEFORE_PUNCT_RE, "$1")
    .trim();
  if (!s) return "";

  let lowered = s.toLowerCase();
  for (const [re, replacement] of PRESERVE_CASE_RULES) {
    lowered = lowered.replace(re, replacement);
  }
  // Keep PO Box and numbered highway routes (US 290, PR 12) capitalized cleanly without touching English "us"
  lowered = lowered.replace(PO_BOX_LOWER_RE, "PO Box");
  lowered = lowered.replace(HIGHWAY_ROUTE_PREFIX_RE, (m) => m.toUpperCase());

  // Capitalize leading alphabetic character when clause does not open with a number ("3 bedrooms", "2nd story")
  lowered = lowered.replace(LEADING_ALPHA_RE, (_, pre, ch) => `${pre}${ch.toUpperCase()}`);
  if (!TERMINAL_PUNCT_RE.test(lowered)) {
    lowered += ".";
  }
  return lowered;
}

function expandAppraiserBody(bodyText) {
  let s = String(bodyText || "").trim();
  if (!s) return "";

  // Protect "PO BOX" before expanding PO -> property owner
  s = s.replace(PO_BOX_PROTECT_RE, "__PO_BOX__");

  // Subarea sketch codes with square footage: e.g. `OFP(120)` or `BAS (1452)` or `BAS(1365801)`
  s = s.replace(SUBAREA_PAREN_RE, (_, code, sqft) => {
    const label = SUBAREA_CODE_LABELS[code.toUpperCase()] || code.toUpperCase();
    return `${label} (${formatCommaInt(sqft)} sq ft)`;
  });

  // Standalone subarea codes after P/U, ADD, ADDED, NEW, DEMO, REMOVED
  s = s.replace(
    SUBAREA_VERB_RE,
    (_, verb, code) => `${verb} ${SUBAREA_CODE_LABELS[code.toUpperCase()] || code}`
  );

  // Condition grade transitions: `FROM PR TO FR`, `CDU @ GD`, `KEPT CDU @ GD`, `CDU: AVG`, `CDU TO GD`
  s = s.replace(GRADE_FROM_TO_RE, (_, _f, g1, _t, g2) => {
    const l1 = GRADE_CODE_LABELS[g1.toUpperCase()] || g1;
    const l2 = GRADE_CODE_LABELS[g2.toUpperCase()] || g2;
    return `from ${l1} to ${l2}`;
  });

  // CDU / PCR condition labels before grade replacements
  for (const [pattern, replacement] of CDU_REPLACEMENTS) {
    s = s.replace(pattern, replacement);
  }

  // Grade after `condition rating` / `@` / `at` / `to` (excluding highway `@ US 59`, `@ PR 12`, and street `@ AVE H` / `@ AVE OF OAKS`)
  s = s.replace(GRADE_AT_RE, (full, prefix, g, offset, fullStr) => {
    const gUpper = g.toUpperCase();
    const label = GRADE_CODE_LABELS[gUpper];
    if (!label) return full;
    const trailing = fullStr.slice(offset + full.length);
    if (
      !CONDITION_RATING_TEST_RE.test(prefix) &&
      ((gUpper === "AVE" && AVE_STREET_SUFFIX_RE.test(trailing)) ||
        ((gUpper === "US" || gUpper === "PR") && ROUTE_NUM_SUFFIX_RE.test(trailing)))
    ) {
      return full;
    }
    const cleanPrefix = prefix
      .replace(AT_SIGN_GLOBAL_RE, "at")
      .replace(WHITESPACE_COLLAPSE_RE, " ")
      .trim();
    return `${cleanPrefix} ${label}`;
  });

  // Reverse grade transitions: `condition rating to Average from FR`
  s = s.replace(
    GRADE_REV_FROM_RE,
    (_, pre, g) => `${pre} ${GRADE_CODE_LABELS[g.toUpperCase()] || g}`
  );

  // `ADJ ... TO <GRADE>` when not street name (exclude `AVE` unless preceded by condition/grade words)
  s = s.replace(
    ADJ_TO_GRADE_RE,
    (_, pre, g) => `${pre} ${GRADE_CODE_LABELS[g.toUpperCase()] || g}`
  );

  // Time, inspection & tax-roll status phrases
  for (const [pattern, replacement] of PHRASE_REPLACEMENTS) {
    s = s.replace(pattern, replacement);
  }
  s = s.replace(PO_NEXT_WORD_RE, (_, nextWord) => `property owner ${nextWord}`);

  // Structural & room abbreviations
  for (const [pattern, replacement] of WORD_REPLACEMENTS) {
    s = s.replace(pattern, replacement);
  }

  // `@ 100%` -> `at 100%`
  s = s.replace(AT_PERCENT_RE, "at $1");

  // Restore PO Box
  s = s.replace(PO_BOX_RESTORE_RE, "PO Box");

  return sentenceCaseClause(s);
}

const DATE_PREFIX_SRC = `\\d{1,2}\\/\\d{1,2}\\/\\d{2,4}|\\d{1,2}\\/\\d{4}|\\d{1,2}\\/\\d{2}(?=\\s*(?:[-:]|$|${INSPECTION_CODE_ALT}\\b))`;

const SEGMENT_HEADER_RE = new RegExp(
  `^(?:(${DATE_PREFIX_SRC})(?:\\s*[-:]\\s*|\\s+|$)(?:(${INSPECTION_CODE_ALT})\\s*[:\\-]\\s*|(${INSPECTION_CODE_ALT})(?:\\s+(?=[A-Z0-9#@])|\\s*$))?|(${INSPECTION_CODE_ALT})\\s*[:\\-]\\s*)(.*)$`,
  "i"
);

/**
 * Decode an HCAD appraiser note into dated Plain-English inspection bullets while
 * preserving the exact verbatim note string.
 *
 * @param {string} rawNote
 * @returns {{
 *   hasShorthand: boolean,
 *   entries: Array<{ date: string, kind: string, text: string }>,
 *   raw: string
 * }}
 */
export function decodeAppraiserNote(rawNote) {
  const raw = String(rawNote ?? "").trim();
  if (!raw) {
    return { hasShorthand: false, entries: [], raw: "" };
  }

  const segments = splitAppraiserSegments(raw);
  const hasShorthand = segments.length > 1 || SHORTHAND_DETECTION_RE.test(raw);
  if (!hasShorthand) {
    return { hasShorthand: false, entries: [], raw };
  }

  const entries = [];
  for (const seg of segments) {
    // Handle `STI HAD: BAS(1452) ...` legacy sketch prefix directly
    const stiMatch = seg.match(STI_HAD_PREFIX_RE);
    if (stiMatch) {
      entries.push({
        date: "",
        kind: "Legacy sketch",
        text: expandAppraiserBody(stiMatch[1]),
      });
      continue;
    }

    const m = seg.match(SEGMENT_HEADER_RE);
    if (!m) {
      entries.push({ date: "", kind: "", text: expandAppraiserBody(seg) });
      continue;
    }
    const rawDate = m[1] || "";
    const rawCode = (m[2] || m[3] || m[4] || "").toUpperCase();
    const rest = (m[5] || "").trim();

    if (!rest && rawCode) {
      entries.push({
        date: rawDate ? formatHcadInspectionDate(rawDate, true) : "",
        kind: INSPECTION_PREFIX_LABELS.get(rawCode) || "",
        text: "Appraisal check logged.",
      });
      continue;
    }

    const kind = rawCode ? INSPECTION_PREFIX_LABELS.get(rawCode) || "" : "";
    const formattedDate = rawDate
      ? formatHcadInspectionDate(rawDate, Boolean(rawCode))
      : "";
    entries.push({
      date: formattedDate,
      kind,
      text: expandAppraiserBody(rest || seg),
    });
  }

  return {
    hasShorthand: entries.length > 0,
    entries,
    raw,
  };
}

const PERMIT_SPEC_LIST_RE = /\bSPEC\s+LIST\b/;
const PERMIT_COM_DEMO_RE = /\bCOM(?:M|MERCIAL)?\b/;
const PERMIT_HCHA_RE = /\bHCHA\b/;
const PERMIT_COH_REHAB_RE = /\bCOH\s*-\s*(?:MINOR|MAJOR)\b/;
const PERMIT_CH19_RE = /\bCH(?:APTER|\.?)\s*19\b/;
const PERMIT_OBY_RE = /\bOB&Y\b/;
const PERMIT_RACM_RE = /\b(?:RACM|NESHAP)\b/;
const PERMIT_4D_RE = /\b4D\b/;
const PERMIT_COM_PHASE_RE = /\bCOM\s+PH\s+(?:I|II|III|1|2|3)\b/;
const PERMIT_TENANT_BUILDOUT_RE =
  /\b(?:LEASE\s+SPACE|BLD-OUT|BLDOUT|BUILD-OUT|BUILDOUT|TENANT\s+FINISH|SHELL\s+BLDG)\b/;
const PERMIT_BLDG_CODE_RE = /\b(?:IRC|IECC|IBC)\b/;
const PERMIT_PV_RE = /\bPV\b/;
const XFEAT_TRAILING_ALPHA_RE = /[A-Z]{2,}$/;
const XFEAT_TRUNC_SUFFIX_RE = /[,/\-][A-Z]{2,6}$/;

const PERMIT_TOKEN_GLOSSARY_RULES = Object.freeze([
  [/\b(?:SFD|SFR)\b/, "single-family residence"],
  [/\bMFD\b/, "multi-family dwelling"],
  [/\bRMDL\b/, "remodel"],
  [/\b(?:ADDN|ADDTN|ADDT)\b/, "building addition"],
  [/\bRPRS\b/, "repairs"],
  [/\bFND\b/, "foundation work"],
  [/\bDET\s+GAR\b/, "detached garage"],
  [/\bATT\s+GAR\b/, "attached garage"],
  [/\bW\/H\b/, "water heater"],
  [/\bW\/D\b/, "washer/dryer"],
  [/\bMEP\b/, "mechanical, electrical, and plumbing"],
]);

/**
 * Return a concise 1-sentence Plain-English explanation for a municipal permit record
 * ONLY when `p.d` or `p.src` contains technical municipal shorthand, program codes, or
 * abbreviations. Returns `""` when the verbatim line is already self-explanatory.
 *
 * @param {{ d?: string, tp?: string, cat?: string, src?: string }} permit
 * @returns {string}
 */
export function explainPermitRecord(permit) {
  if (!permit || typeof permit !== "object") return "";
  const desc = String(permit.d ?? "").trim();
  if (!desc) return "";
  const u = desc.toUpperCase();

  if (u.includes("OMITTED IMPS") || u.includes("OMITTED IMPROVEMENTS")) {
    return "Previously unassessed building or addition added retroactively to the HCAD tax roll (not a new City permit).";
  }

  if (u.includes("ONE-STOP") || u.includes("ONE STOP")) {
    if (u.includes("RE-ROOF") || u.includes("REROOF") || u.includes("ROOF")) {
      return "Fast-track City of Houston One-Stop roof replacement permit (same-day review for non-structural roof work).";
    }
    if (u.includes("REMODEL") || u.includes("RMDL")) {
      return "Fast-track City of Houston residential remodel permit (streamlined review for non-structural interior or exterior updates).";
    }
    return "Fast-track City of Houston One-Stop permit (same-day review for minor, non-structural residential work).";
  }

  if (u.includes("AS PER SPEC LIST") || u.includes("PER SPEC LIST") || PERMIT_SPEC_LIST_RE.test(u)) {
    return "Itemized residential repair scope filed on the City of Houston repair specification checklist (commonly foundation, framing, siding, or storm repairs).";
  }

  if (u.includes("DEMO RES/SEWER DISC") || (u.includes("SEWER") && u.includes("DISC"))) {
    if (!u.includes("DEMO RES") && PERMIT_COM_DEMO_RE.test(u)) {
      return "Commercial building demolition and municipal sewer line cap-off permit.";
    }
    return "Residential teardown and municipal sewer line disconnection permit (pre-demolition utility cap-off).";
  }

  if (u.includes("INTERIOR") && u.includes("NON-LOAD BEARING")) {
    return "Interior non-structural partition remodel only (not a full building demolition).";
  }

  if (PERMIT_HCHA_RE.test(u)) {
    return "Houston Housing Authority / disaster housing rehabilitation program repair permit.";
  }

  if (PERMIT_COH_REHAB_RE.test(u)) {
    return "City of Houston housing rehabilitation program repair permit.";
  }

  if (
    u.includes("FLOOD PROJECT") ||
    u.includes("FLOODPLAIN") ||
    PERMIT_CH19_RE.test(u)
  ) {
    return "City of Houston Chapter 19 floodplain repair, mitigation, or elevation compliance permit.";
  }

  if (u.includes("SUBAREAS (581") || u.startsWith("SUBAREAS (")) {
    return "HCAD appraisal sketch update recording added building subareas (porches, garages, or room additions).";
  }

  if (u.includes("EXTRA FEATURES (OB&Y)") || PERMIT_OBY_RE.test(u)) {
    return "HCAD outbuilding and yard feature record (pool, detached shed/garage, canopy, patio, or paving).";
  }

  if (
    PERMIT_RACM_RE.test(u) ||
    String(permit.src || "").toUpperCase().includes("DSHS")
  ) {
    return "State-mandated Texas DSHS asbestos survey or abatement filing (required 10 working days before demolition or major renovation).";
  }

  if (PERMIT_4D_RE.test(u) || u.includes("DANGEROUS BLDG") || u.includes("NEIGHBORHOOD PROTECTION")) {
    return "City of Houston Neighborhood Protection dangerous-building enforcement / demolition order.";
  }

  if (PERMIT_COM_PHASE_RE.test(u) || PERMIT_TENANT_BUILDOUT_RE.test(u)) {
    return "Commercial interior tenant build-out or phased construction inside an existing building shell.";
  }

  if (PERMIT_BLDG_CODE_RE.test(u)) {
    return "Reviewed under International Residential, Building, or Energy Conservation Code standards.";
  }

  // General municipal token expansions when cryptic abbreviations appear in `p.d`
  const tokenGlossary = [];
  for (const [re, label] of PERMIT_TOKEN_GLOSSARY_RULES) {
    if (re.test(u)) tokenGlossary.push(label);
  }
  if (PERMIT_PV_RE.test(u) && u.includes("SOLAR")) {
    tokenGlossary.push("photovoltaic solar array");
  }

  if (tokenGlossary.length > 0) {
    return `Includes ${tokenGlossary.join(", ")}.`;
  }

  return "";
}

/**
 * Return a concise Plain-English explanation for an HCAD 40-char extra-feature (`xf.d`)
 * line when truncated or CAMA-coded; returns `""` when already self-explanatory.
 *
 * @param {string} rawDesc
 * @returns {string}
 */
export function explainExtraFeatureDesc(rawDesc) {
  const desc = String(rawDesc ?? "").trim();
  if (!desc) return "";
  const u = desc.toUpperCase();

  if (u.startsWith("CRACKED/DAMAGED CONC") || (u.includes("CRACKED") && u.includes("SLAB"))) {
    return "HCAD structural depreciation adjustment noting foundation or concrete slab cracking/settlement.";
  }
  if (u.includes("SWIMMING POOL") && u.includes("GUNITE")) {
    return "In-ground shotcrete/concrete (gunite) residential swimming pool.";
  }
  if (u.startsWith("CANOPY - ROOF EXTENSION")) {
    return "Attached patio cover or roofline extension.";
  }
  if (u.startsWith("FRAME UTILITY SHED")) {
    return "Detached wood-frame storage or utility shed.";
  }
  if (u.startsWith("RESIDENTIAL REINFORCED CONCRETE PAVING") || u.includes("REINF CONC PAV")) {
    return "Reinforced concrete driveway, flatwork, or site paving.";
  }
  if (u.includes("BULKHEAD")) {
    return "Waterfront or drainage shoreline retaining wall (bulkhead).";
  }
  if (desc.length >= 39 && XFEAT_TRAILING_ALPHA_RE.test(u) && XFEAT_TRUNC_SUFFIX_RE.test(u)) {
    return "40-character HCAD appraisal table entry (label truncated in county CAMA export).";
  }
  return "";
}

/**
 * Return a concise Plain-English explanation for a City of Houston (`coh_permits`)
 * early-warning permit entry in `active_permit_alerts.json` or `monitor_activity_log.json`.
 *
 * @param {Record<string, unknown> | null | undefined} cp
 * @returns {string}
 */
export function explainCohWatchPermit(cp) {
  if (!cp || typeof cp !== "object") return "";
  const permitType = String(cp.permit_type ?? "")
    .trim()
    .toUpperCase();
  const stage = String(cp.permit_stage ?? "")
    .trim()
    .toLowerCase();
  const typeLabel = String(cp.permit_type_label ?? "").trim();
  const useDesc = String(cp.use_desc ?? cp.permit_desc ?? "").trim();
  const upperDesc = useDesc.toUpperCase();

  const isSewerDisconnect =
    Boolean(cp.is_sewer_disconnect) ||
    stage === "stage_1_sewer_plumbing" ||
    permitType === "12" ||
    permitType === "4P" ||
    /\b1\s*OF\s*2\b/i.test(typeLabel);

  const isGarageAptOnly = /\bGARAGE\s+APT\s+ONLY\b/i.test(upperDesc);
  const isNoSdRequired =
    /\bNO\s+SD\s+REQ(?:UIRED)?\b|\bNO\s+SEWER\s+DISC(?:ONNECT)?\s+REQ/i.test(upperDesc);
  const isGarageOnly =
    Boolean(cp.is_garage_only) ||
    stage === "accessory_only" ||
    isGarageAptOnly ||
    /\b(?:GARAGE|CARPORT|ACCESSORY|SHED|POOL)\s*(?:APT\s*)?ONLY\b/i.test(upperDesc) ||
    /\(NO\s+SD\s+REQUIRED\)\s*-\s*GA/i.test(upperDesc);

  const isInteriorOnly =
    Boolean(cp.is_interior_only) ||
    permitType === "PX" ||
    /\bNON[-\s]*LOAD\s+BEARING\b|\bNON[-\s]*FIRE\s+R|\bINTERIOR\s+DEMO|\bPPR\s+DEMO\b/i.test(
      upperDesc
    );

  const isDangerousBldg =
    permitType === "4D" ||
    /\bCOH\s+D\.?B\.?\b|\bDAP\s+LIST\b|\bDANGEROUS\s+B(?:LDG|UILDING)\b/i.test(upperDesc);

  if (isInteriorOnly) {
    if (permitType === "PX" || /\bPLAN\s+REVIEW\s+FEE\b/i.test(typeLabel)) {
      return "City of Houston commercial plan-review fee (Type PX) for interior non-load-bearing, non-fire-rated partition removal only (not a structural building demolition).";
    }
    if (/\bPPR\b/i.test(upperDesc)) {
      return "Private Provider Review (PPR) commercial building permit (Type 13) for interior non-load-bearing, non-fire-rated partition removal only (not a structural building demolition).";
    }
    return "Interior non-load-bearing partition or tenant remodel permit only (not a structural building demolition).";
  }

  if (isGarageAptOnly) {
    if (isSewerDisconnect) {
      return "Stage 1 pre-demolition plumbing permit to cap the sewer line for a detached garage apartment on the parcel (the main residence is not covered by this permit).";
    }
    return "Demolition permit limited to a detached garage apartment on the parcel (the main residence is not covered by this permit).";
  }

  if (isGarageOnly) {
    if (isNoSdRequired) {
      return "Detached garage or accessory structure teardown permit (no separate sewer disconnection permit required because the structure has no plumbing tap; main house is not covered).";
    }
    if (isSewerDisconnect) {
      return "Stage 1 pre-demolition plumbing permit to cap the sewer line for a detached garage or accessory structure teardown (main building is not covered by this permit).";
    }
    return "Demolition permit limited to a detached garage, carport, or accessory structure (the main building on the parcel is not covered by this permit).";
  }

  if (isDangerousBldg) {
    return "City of Houston Dangerous Building (Code Enforcement / Demolition Assistance Program Type 4D) structural demolition permit authorizing full building teardown.";
  }

  if (isSewerDisconnect) {
    return "Stage 1 pre-demolition plumbing permit (Type 12/4P) to cap the municipal sewer line; a separate Stage 2 Building Demolition (SD) permit is still required before structural teardown.";
  }

  if (
    stage === "stage_2_demolition" ||
    permitType === "SD" ||
    Boolean(cp.is_structural_demo) ||
    /\b(?:BLD|RES|BUILDING|RESIDENTIAL)\s+DEMO\b|\bDEMO\s+(?:RES|BLD)\b/i.test(upperDesc)
  ) {
    return "Stage 2 structural demolition permit (Type SD) authorizing physical teardown of the building now that sewer disconnection is cleared.";
  }

  return explainPermitRecord({
    d: useDesc,
    tp: typeLabel,
    cat: cp.is_structural_demo ? "demo" : "other",
  });
}

/**
 * Return a concise Plain-English explanation for a Texas DSHS (`dshs_notifications`)
 * asbestos or demolition filing in `active_permit_alerts.json` or `monitor_activity_log.json`.
 *
 * @param {Record<string, unknown> | null | undefined} dn
 * @returns {string}
 */
export function explainDshsNotification(dn) {
  if (!dn || typeof dn !== "object") return "";
  const workType = String(dn.work_type ?? "").trim();
  const workDesc = String(dn.work_description ?? "").trim();
  const containment = String(dn.containment_method ?? "").trim();
  const combined = `${workType} ${workDesc} ${containment}`.trim();
  if (!combined) return "";

  const upper = combined.toUpperCase();

  if (/\bDEMOLITION\b/i.test(workType) && !/\bABATEMENT\b/i.test(workType)) {
    return "State-mandated 10-working-day advance notice of structural demolition filed with Texas DSHS under federal NESHAP asbestos regulations.";
  }

  if (/\bO\s*&\s*M\s+WORK\b|\bOPERATIONS?\s*(?:AND|&)\s*MAINTENANCE\b/i.test(upper)) {
    return "Routine Operations and Maintenance (O&M) asbestos management filing for localized building maintenance (not a full building teardown).";
  }

  if (
    /\b296\.212\b|\bFULL\s+CONTAINMENT\b|\bNEGATIVE\s+(?:AIR\s+)?PRESSURE\b|\bD-CON\b|\bDECON(?:TAMINATION)?\b/i.test(
      upper
    )
  ) {
    if (
      /\bFLOOR\s+TILE\b|\bFLOOR(?:ING)?\s+AND\s+ASSOC|\bMASTIC\b|\bNON[-\s]*FRIABLE\b/i.test(
        workDesc
      ) &&
      !/\bFIREPROOFING\b|\bPIPE\s+INSULATION\b|\bSPRAY\s+APPLIED\b/i.test(workDesc)
    ) {
      return "State-mandated 10-working-day advance notice to remove asbestos floor tile or adhesive mastic under sealed containment (25 TAC Sec. 296.212) before interior renovation or demolition.";
    }
    return "State-mandated 10-working-day advance asbestos abatement filing under sealed negative-pressure containment (25 TAC Sec. 296.212) to strip regulated asbestos materials before renovation or demolition.";
  }

  if (
    /\bNON[-\s]*FRIABLE\b|\bCATEGORY\s+(?:I|II|1|2)\b|\bFLOOR\s+TILE\b|\bMASTIC\b|\bRFCI\b/i.test(
      upper
    )
  ) {
    return "State-mandated 10-working-day advance notice to remove non-friable asbestos-containing floor tile, adhesive mastic, or caulking before renovation or demolition.";
  }

  return "State-mandated 10-working-day advance notice to remove Regulated Asbestos-Containing Material (RACM) before renovation or demolition work begins.";
}

/**
 * Return a concise Plain-English explanation for a `multi_agency_filings` entry
 * in `active_permit_alerts.json`.
 *
 * @param {Record<string, unknown> | null | undefined} mf
 * @returns {string}
 */
export function explainMultiAgencyFiling(mf) {
  if (!mf || typeof mf !== "object") return "";
  const agency = String(mf.agency ?? "").trim();
  const status = String(mf.status ?? "").trim();
  const title = String(mf.title ?? "").trim();
  const details = String(mf.details ?? "").trim();
  const combined = `${agency} ${status} ${title} ${details}`.trim();
  if (!combined) return "";

  if (/\bTDLR\b|\bTABS\b|\bARCHITECTURAL\s+BARRIERS\b/i.test(combined)) {
    return "Texas Department of Licensing and Regulation (TDLR) Architectural Barriers registration required for commercial construction or renovation projects over $50,000.";
  }
  if (/\b311\b|\bDANGEROUS\s+BUILDINGS?\b|\bDONDB\b/i.test(combined)) {
    return "City of Houston Department of Neighborhoods (DON) Dangerous Building investigation for structural hazard or code enforcement.";
  }
  if (/\bDPV\b|\bDEVELOPMENT\s+PLAT\s+(?:WITH\s+)?VARIANCE\b/i.test(combined)) {
    if (/\bCMU\b/i.test(combined)) {
      return "Chapter 42 Development Plat Variance (Section K DPV) requesting a building-line or site-design exception (CMU = concrete masonry unit / cinder-block wall).";
    }
    return "Chapter 42 Development Plat Variance (Section K DPV) requesting a setback, building line, or site-design exception before building permits issue.";
  }
  if (/\bC3N\b|\bCLASS\s+3\b.*\bREPLAT\b/i.test(combined)) {
    return "Chapter 42 Class 3 Subdivision Replat (C3N) requiring mailed public hearing notices to neighboring property owners before replatting existing lots.";
  }
  if (/\bC2R\b|\bCLASS\s+2\b.*\bREPLAT\b/i.test(combined)) {
    return "Chapter 42 Class 2 Subdivision Replat (C2R) reviewed on the Planning Commission replat docket.";
  }
  if (/\bC3F\b|\bCLASS\s+3\b.*\bFINAL\s+PLAT\b/i.test(combined)) {
    return "Chapter 42 Class 3 Final Subdivision Plat (C3F) on the Planning Commission consent docket.";
  }
  if (/\bTABC\b|\bLIQUOR\b|\bALCOHOL\b/i.test(combined)) {
    return "Texas Alcoholic Beverage Commission (TABC) license or permit filing indicating new hospitality or retail tenant activity.";
  }
  return "";
}

const QUIET_WATCHLIST_CHECK_RE =
  /^(?:no\s+(?:new\s+|active\s+)?(?:results?|records?|demo\s+permits?|filings?)(?:\s+found)?\.?|none\s+found\.?|not\s+yet\.?|n\/a|clear|old\/irrelevant\s+results?\.?)$/i;

/**
 * Return a concise Plain-English explanation for a Watchlist 15-point check finding
 * (`chk-01`..`chk-15`) or `monitor_activity_log.json` event summary string.
 * Returns `""` on quiet/routine strings so quiet check rows stay uncluttered.
 *
 * @param {string} rawText
 * @param {string} [checkIdOrKind]
 * @returns {string}
 */
export function explainWatchlistCheckText(rawText, checkIdOrKind = "") {
  const clean = String(rawText ?? "").trim();
  const tag = String(checkIdOrKind ?? "")
    .trim()
    .toLowerCase();

  if (
    !clean ||
    tag === "watchlist_sweep_degraded" ||
    QUIET_WATCHLIST_CHECK_RE.test(clean) ||
    /\b(?:no\s+new\b|0\s+active\b|no\s+matching\b|no\s+open\b|none\s+found\b|confirmed\s+match\s+to\s+current\s+owner\b|no\s+(?:new\s+|active\s+)?(?:filings?|results?|records?|demo\s+permits?|cases?|projects?|registrations?|notifications?|violations?)(?:\s+found)?)/i.test(
      clean
    ) ||
    /\b\d+\s+agency\s+endpoint\(s\)\s+did\s+not\s+complete\s+cleanly\b|\bdaily\s+\d+-agency\s+sweep\s+degraded\b/i.test(
      clean
    )
  ) {
    return "";
  }

  // 1. City of Houston Demolition / Sewer Disconnect / PPR Demo shorthand
  if (
    /\b(?:BLD|RES|DEMO)\s+(?:DEMO|RES|BLD)\s*\/\s*SEWER\s+DISC/i.test(clean) ||
    /\bCOH\s+D\.?B\.?\s*\/\s*RESIDENTIAL\s+DEMO/i.test(clean) ||
    /\bPPR\s+DEMO\s+OF\s+NON[-\s]*LOAD\s+BEARING/i.test(clean) ||
    /\bGARAGE\s+(?:APT\s+)?ONLY\b/i.test(clean) ||
    /\(NO\s+SD\s+REQUIRED\)/i.test(clean)
  ) {
    const isSewerStage1 =
      /\b1\s*OF\s*2\b|\bSEWER\s+DISCONNECT\s+PLUMBING\b/i.test(clean) &&
      !/\b2\s*OF\s*2\b/i.test(clean);
    const isDemoStage2 = /\b2\s*OF\s*2\b|\bBUILDING\s+DEMOLITION\s+PERMIT\b/i.test(clean);
    if (
      /\bGARAGE\s+APT\s+ONLY\b/i.test(clean) ||
      /\bGARAGE\s+ONLY\b/i.test(clean) ||
      /\(NO\s+SD\s+REQUIRED\)/i.test(clean) ||
      /\bPPR\s+DEMO\b|\bNON[-\s]*LOAD\s+BEARING\b/i.test(clean) ||
      /\bCOH\s+D\.?B\.?\b|\bDAP\s+LIST\b/i.test(clean) ||
      isSewerStage1 ||
      isDemoStage2
    ) {
      return explainCohWatchPermit({
        use_desc: clean,
        permit_type: /\bCOH\s+D\.?B\.?\b|\bDAP\s+LIST\b/i.test(clean)
          ? "4D"
          : isDemoStage2
          ? "SD"
          : isSewerStage1
          ? "12"
          : "",
        is_sewer_disconnect: isSewerStage1,
        is_structural_demo: isDemoStage2,
      });
    }
    return "City of Houston pre-demolition sewer disconnection (Type 12/4P) or structural building demolition (Type SD) permit filing.";
  }

  // 2. Texas DSHS Asbestos & Demolition notifications
  if (
    tag === "chk-15" ||
    tag === "permit_dshs_alert" ||
    /\bDSHS\s+NOTIFICATION\s*#/i.test(clean) ||
    /\bTX\s+DSHS\s+(?:ABATEMENT|DEMOLITION)\s+FILING\b/i.test(clean) ||
    /\bRACM\b/i.test(clean)
  ) {
    const isDemoFiling =
      /\bTX\s+DSHS\s+DEMOLITION\s+FILING\b/i.test(clean) && !/\bABATEMENT\b/i.test(clean);
    return explainDshsNotification({
      work_type: isDemoFiling ? "Demolition" : "Abatement",
      work_description: clean,
    });
  }

  // 3. TDLR TABS, Houston 311 Dangerous Buildings, and Chapter 42 (`DPV`, `C3N`, `C2R`, `C3F`)
  if (
    tag === "chk-10" ||
    /\bTDLR\s+(?:PROJECT\s+|TABS\s*)?#?TABS\d+/i.test(clean) ||
    /\bARCHITECTURAL\s+BARRIERS\s*\(TABS\)/i.test(clean)
  ) {
    return explainMultiAgencyFiling({ agency: "TDLR TABS", title: clean });
  }
  if (
    tag === "chk-13" ||
    /\bHOUSTON\s+311\b.*\bDANGEROUS\s+BUILDINGS?\b/i.test(clean)
  ) {
    return explainMultiAgencyFiling({ agency: "Houston 311 Dangerous Buildings", title: clean });
  }
  if (
    /\bDPV\b|\bDEVELOPMENT\s+PLAT\s+(?:WITH\s+)?VARIANCE\b|\bC3N\b|\bC2R\b|\bC3F\b|\bCLASS\s+[23]\b.*\b(?:REPLAT|FINAL\s+PLAT)\b/i.test(
      clean
    )
  ) {
    const ch42Note = explainMultiAgencyFiling({ agency: "Planning Commission", title: clean });
    if (ch42Note) return ch42Note;
  }

  // 6. HCAD Ownership & Classification Sweep (`Impr $0`)
  if (
    /\bIMPR\s+\$0\b/i.test(clean) &&
    (tag === "chk-05" || /\bCLASS\s+[A-Z0-9]+\b|\bHCAD\b/i.test(clean))
  ) {
    return "HCAD appraisal roll lists $0 improvement value (land-only assessment or structure value zeroed out on the current tax roll).";
  }

  // 7. HAHC Certificate of Remediation (`COR`)
  if (/\bCERTIFICATE\s+OF\s+REMEDIATION\b|\bDENIAL\s+OF\s+COA,\s*COR\b/i.test(clean)) {
    return "HAHC denied the Certificate of Appropriateness (COA) and issued a Certificate of Remediation (COR) requiring unpermitted or non-compliant work to be corrected.";
  }

  return "";
}

/**
 * Render the complete innerHTML of `#inspector-permits-body` with strict HTML escaping,
 * Plain-English HCAD appraiser note decoding + collapsible verbatim disclosure, and
 * conditional Plain-English permit / extra-feature sub-lines.
 *
 * @param {{
 *   origYr?: number,
 *   remodYr?: number,
 *   effYr?: number,
 *   qualGrade?: string,
 *   relocNote?: string,
 *   appraiserNote?: string,
 *   origPermit?: string,
 *   origCost?: string,
 *   builder?: string,
 *   permitsList?: Array<Record<string, unknown>>,
 *   extraFeatures?: Array<Record<string, unknown>>,
 *   atlasMaxYear?: number,
 * }} params
 * @returns {string}
 */
export function buildInspectorPermitsBodyHtml({
  origYr = 0,
  remodYr = 0,
  effYr = 0,
  qualGrade = "",
  relocNote = "",
  appraiserNote = "",
  origPermit = "",
  origCost = "",
  builder = "",
  permitsList = [],
  extraFeatures = [],
  atlasMaxYear = 2026,
} = {}) {
  const summaryPills = [];
  if (origYr > 1800) {
    summaryPills.push(
      `<span class="permit-stat-pill">Original Erection: <strong>${escapePermitHtml(origYr)}</strong></span>`
    );
  }
  if (remodYr > 1836) {
    summaryPills.push(
      `<span class="permit-stat-pill highlight">HCAD Remodel Year: <strong>${escapePermitHtml(remodYr)}</strong></span>`
    );
  }
  if (effYr > 1836 && effYr !== origYr) {
    summaryPills.push(
      `<span class="permit-stat-pill">Effective Age Basis: <strong>${escapePermitHtml(effYr)}</strong></span>`
    );
  }
  if (qualGrade) {
    summaryPills.push(
      `<span class="permit-stat-pill">Quality: <strong>${escapePermitHtml(qualGrade)}</strong></span>`
    );
  }

  let html = "";
  if (summaryPills.length > 0) {
    html += `<div class="permit-summary-badges">${summaryPills.join("")}</div>`;
  }

  const cleanReloc = String(relocNote || "").trim();
  const cleanNote = String(appraiserNote || "").trim();

  if (cleanReloc) {
    html += `<div class="permit-appraiser-note reloc">
      <strong>&#128666; Documented Structure Relocation:</strong> ${escapePermitHtml(cleanReloc)}
    </div>`;
  }

  if (cleanNote && cleanNote !== cleanReloc) {
    const decoded = decodeAppraiserNote(cleanNote);
    if (decoded.hasShorthand && decoded.entries.length > 0) {
      const itemsHtml = decoded.entries
        .map((entry) => {
          const prefixParts = [];
          if (entry.date) prefixParts.push(escapePermitHtml(entry.date));
          if (entry.kind) {
            prefixParts.push(
              entry.date
                ? `(${escapePermitHtml(entry.kind)})`
                : escapePermitHtml(entry.kind)
            );
          }
          const prefixHtml =
            prefixParts.length > 0
              ? `<strong class="permit-appraiser-entry-lead">${prefixParts.join(" ")}:</strong> `
              : "";
          return `<li class="permit-appraiser-decoded-item">${prefixHtml}${escapePermitHtml(entry.text)}</li>`;
        })
        .join("");
      html += `<div class="permit-appraiser-note">
        <div class="permit-appraiser-header"><strong>HCAD Appraiser Permit / Renovation Note</strong></div>
        <ul class="permit-appraiser-decoded-list">${itemsHtml}</ul>
        <details class="permit-verbatim-details">
          <summary class="permit-verbatim-summary">Verbatim HCAD text</summary>
          <div class="permit-verbatim-raw">${escapePermitHtml(decoded.raw)}</div>
        </details>
      </div>`;
    } else {
      html += `<div class="permit-appraiser-note">
        <strong>HCAD Appraiser Permit / Renovation Note:</strong> ${escapePermitHtml(cleanNote)}
      </div>`;
    }
  }

  const timelineItems = [];
  if (origPermit || origCost) {
    const origParts = [
      origPermit ? `Permit ${escapePermitHtml(origPermit)}` : "Original Building Permit",
    ];
    if (origCost) {
      origParts.push(`Contract / Valuation: ${escapePermitHtml(origCost)}`);
    }
    if (builder) {
      origParts.push(`Contractor: ${escapePermitHtml(builder)}`);
    }
    timelineItems.push(`
      <div class="permit-timeline-item">
        <div class="permit-timeline-top">
          <span class="permit-cat-badge cat-new">Original Construction Permit</span>
          <span class="permit-date-mono">${escapePermitHtml(origYr || "Historic")}</span>
        </div>
        <div class="permit-desc-text">${origParts.join(" &middot; ")}</div>
      </div>
    `);
  }

  const safePermits = Array.isArray(permitsList) ? permitsList : [];
  for (const p of safePermits) {
    if (!p || typeof p !== "object") continue;
    const rawCat = String(p.cat || "other")
      .trim()
      .toLowerCase();
    const cat = VALID_PERMIT_CAT_CODES.has(rawCat) ? rawCat : "other";
    const tp = String(p.tp || "Building Permit").trim();
    const dt = String(p.dt || "").trim();
    const desc = String(p.d || "").trim();
    const pid = String(p.id || "").trim();
    const srcLabel = normalizePermitSourceLabel(p.src);

    const metaParts = [];
    if (dt) metaParts.push(escapePermitHtml(dt));
    if (pid) metaParts.push(`#${escapePermitHtml(pid)}`);
    if (srcLabel) metaParts.push(escapePermitHtml(srcLabel));

    const plainSub = explainPermitRecord(p);
    const itemModifier =
      cat === "demo"
        ? "permit-item-demo"
        : cat === "asbestos"
        ? "permit-item-asbestos"
        : "";

    timelineItems.push(`
      <div class="permit-timeline-item ${itemModifier}">
        <div class="permit-timeline-top">
          <span class="permit-cat-badge cat-${cat}">${escapePermitHtml(tp)}</span>
          <span class="permit-date-mono">${metaParts.join(" &middot; ")}</span>
        </div>
        ${desc ? `<div class="permit-desc-text">${escapePermitHtml(desc)}</div>` : ""}
        ${
          plainSub
            ? `<div class="permit-plain-english-sub">${escapePermitHtml(plainSub)}</div>`
            : ""
        }
      </div>
    `);
  }

  const safeXfeat = Array.isArray(extraFeatures) ? extraFeatures : [];
  for (const xf of safeXfeat) {
    if (!xf || typeof xf !== "object") continue;
    const xfDesc = String(xf.d || "").trim();
    const xfYr = xf.y ? String(xf.y) : "Dated";
    const xfPlain = explainExtraFeatureDesc(xfDesc);
    timelineItems.push(`
      <div class="permit-timeline-item xf-item">
        <div class="permit-timeline-top">
          <span class="permit-cat-badge cat-aux">Dated Site Feature / Sub-Structure</span>
          <span class="permit-date-mono">${escapePermitHtml(xfYr)}</span>
        </div>
        ${xfDesc ? `<div class="permit-desc-text">${escapePermitHtml(xfDesc)}</div>` : ""}
        ${
          xfPlain
            ? `<div class="permit-plain-english-sub">${escapePermitHtml(xfPlain)}</div>`
            : ""
        }
      </div>
    `);
  }

  if (timelineItems.length > 0) {
    html += `<div class="permit-timeline-list">${timelineItems.join("")}</div>`;
  } else if (!cleanNote && !cleanReloc) {
    html += `<div class="permit-empty-note">No itemized 2004-${escapePermitHtml(
      atlasMaxYear
    )} municipal building permits recorded in HCAD's CAMA permit table for this parcel. Know of a historic building permit or contractor record? Click <strong>Suggest a Date / Shape / Data Correction</strong> below.</div>`;
  }

  return html;
}
