/**
 * Pure helper utilities for Harris County Clerk Real Property (RP.aspx) and
 * Maps & Condos (MC.aspx) archival searches in Step 3 of the House History Research Kit.
 *
 * Handles:
 *   1. Separation of HCAD Appraisal Market Neighborhoods (e.g. "NORHILL") from
 *      recorded legal plat descriptions in HCAD legal_lines (e.g. "NORTH NORHILL").
 *   2. Extraction of all 8 distinct County Clerk legal description search boxes:
 *      Subdivision or Description, Section, Lot (separate from Tract), Block,
 *      Unit, Abstract, Outlot, Tract, and Reserve, plus Root Plat Volume & Page.
 *   3. Sanitization of HCAD owner strings to satisfy the County Clerk's strict
 *      "Last Name First - No Punctuation" Grantee/Grantor rule.
 *   4. Decoding of compact HCAD recorded deed chain strings ("YYYYMMDD:TOKEN|...")
 *      from permits_<prefix3>.json shards into 1-click Clerk File Number tokens.
 */

const ENTITY_KEYWORD_PATTERNS = [
  // Corporate & legal entity suffixes (dotted L.P. / P.C. collapse before matching; bare spaced "L P" omitted for initials)
  "LLC", "L\\s+L\\s+C", "PLLC", "P\\s+L\\s+L\\s+C", "INC", "INCORPORATED", "CORP", "CORPORATION",
  "COMPANY", "CO", "LP", "LTD", "LIMITED", "LLP", "L\\s+L\\s+P", "PLC", "PC",
  // Financial & community associations
  "BANK", "NATIONAL", "FSB", "CREDIT\\s+UNION",
  "ASSOC", "ASSN", "ASSOCIATION", "HOA", "POA", "CONDO", "CONDOMINIUM",
  // Religious & educational institutions
  "CHURCH", "MINISTRIES", "MINISTRY", "PARISH", "DIOCESE", "ARCHDIOCESE",
  "TEMPLE", "SYNAGOGUE", "MOSQUE", "CHAPEL", "BAPTIST", "METHODIST",
  "CATHOLIC", "PRESBYTERIAN", "LUTHERAN", "EPISCOPAL",
  "ISD", "SCHOOL", "ACADEMY", "UNIVERSITY", "COLLEGE", "INSTITUTE",
  // Government, utilities & civic foundations
  "CITY\\s+OF", "COUNTY\\s+OF", "STATE\\s+OF", "STATE\\s+DEPT", "DEPT\\s+OF",
  "DEPARTMENT", "UNITED\\s+STATES", "USA", "TEXAS\\s+DEPT", "TXDOT",
  "HCFCD", "METRO", "AUTHORITY", "DISTRICT",
  "FOUNDATION", "ENDOWMENT", "CHARITY", "MUSEUM", "HOSPITAL", "CLINIC", "MEDICAL",
  // Commercial real estate, builders & railroads
  "PARTNERS", "PARTNERSHIP", "PROPERTIES", "PROPERTY", "HOLDINGS", "HOLDING",
  "INVESTMENTS", "INVESTMENT", "INVESTORS", "ENTERPRISES", "ENTERPRISE",
  "DEVELOPMENT", "DEVELOPERS", "REALTY", "REAL\\s+ESTATE",
  "HOMES", "HOMEBUILDERS", "BUILDERS", "CONSTRUCTION", "VENTURES", "CAPITAL",
  "GROUP", "MANAGEMENT", "MGMT", "SERVICES", "SOLUTIONS", "SYSTEMS", "OPERATIONS",
  "RETAIL", "COMMERCIAL", "RESIDENTIAL", "APARTMENTS", "VILLAS", "TOWNHOMES",
  "LOFTS", "PLAZA", "CENTER", "CENTRE", "BUILDS", "BUILDER", "RR", "RY", "RAILROAD", "RAILWAY",
];

const ENTITY_KEYWORD_RE = new RegExp(
  `\\b(?:${ENTITY_KEYWORD_PATTERNS.join("|")})\\b`,
  "i"
);

const TRUST_ENTITY_RE =
  /\b(?:TRUSTEES?|LIVING\s+TRUST|REVOCABLE\s+TRUST|FAMILY\s+TRUST|IRREVOCABLE\s+TRUST|CHILDRENS\s+TRUST|TRUST|TR)\b/i;

const NAME_SUFFIX_TOKENS = new Set([
  "JR",
  "SR",
  "II",
  "III",
  "IV",
  "V",
  "MD",
  "DDS",
  "PHD",
  "ESQ",
  "CPA",
]);

const BOILERPLATE_TRAILING_RE =
  /\b(?:ET\s*AL|ETAL|ET\s*UX|ETUX|ET\s*VIR|ETVIR|ESTATE\s+OF|ESTATE|HEIRS|TRUSTEES|TRUSTEE|LIVING\s+TRUST|REVOCABLE\s+TRUST|FAMILY\s+TRUST|IRREVOCABLE\s+TRUST|CHILDRENS\s+TRUST|TRUST|TR|TRS|LIFE\s+ESTATE|LIFE\s+EST)\b/gi;

const OWNER_CARE_OF_TAIL_RE =
  /\s*(?:(?<!\d)%|\bC\s*\/\s*O\b|\bCARE\s+OF\b|\bATTN\s*:?).*$/i;

const OWNER_DBA_TAIL_RE = /\s*\(DBA\s+[^)]*\)\s*$/i;

// Keep 1:1 synchronized with CONFIDENTIAL_OWNER_TOKENS and is_confidential_owner_token in pipeline/atlas_pipeline/schema.py.
export const CONFIDENTIAL_OWNER_TOKENS = new Set([
  "CURRENT OWNER",
  "CONFIDENTIAL",
  "CONFIDENTIAL OWNER",
  "PROTECTED",
  "PROTECTED OWNER",
  "WITHHELD",
  "REDACTED",
]);

export const PLACEHOLDER_OWNER_TOKENS = new Set([
  ...CONFIDENTIAL_OWNER_TOKENS,
  "ET AL",
  "TRUSTEE",
  "ESTATE OF",
  "UNKNOWN",
  "UNKNOWN OWNER",
  "OWNER",
  "PUBLIC / UNLISTED",
  "PUBLIC UNLISTED",
  "UNLISTED",
  "OWNER OF RECORD",
  "OWNER OF RECORD HCAD",
  "PENDING",
  "NONE",
  "NA",
  "N A",
  "NULL",
  "0",
]);

const CONFIDENTIAL_OWNER_PHRASE_RE =
  /\b(?:CURRENT\s+OWNER|CONFIDENTIAL(?:\s+OWNER)?|PROTECTED\s+OWNER|PROTECTED\s+INDIVIDUAL\s+HOMESTEAD|SEC\.?\s*25\.025|WITHHELD|REDACTED)\b/i;

/**
 * Return true if `rawName` carries a statutory TX Tax Code Sec. 25.025 confidentiality marker (`DF-1`).
 * Synchronized 1:1 with `is_confidential_owner_token` in `pipeline/atlas_pipeline/schema.py`.
 */
export function isConfidentialOwnerName(rawName = "") {
  const raw = String(rawName || "").trim();
  if (!raw) return false;
  const stripped = raw.replace(OWNER_CARE_OF_TAIL_RE, "").trim();
  if (!stripped) return false;
  if (CONFIDENTIAL_OWNER_PHRASE_RE.test(stripped)) return true;
  const cleaned = stripped
    .replace(/[^A-Za-z0-9\s&/]+/g, " ")
    .toUpperCase()
    .replace(/\s+/g, " ")
    .trim();
  if (!cleaned) return false;
  if (CONFIDENTIAL_OWNER_TOKENS.has(cleaned)) return true;
  const segments = cleaned.split(/\s*(?:&|\/|\bAND\b)\s*/);
  for (const seg of segments) {
    if (CONFIDENTIAL_OWNER_TOKENS.has(seg.trim())) return true;
  }
  return false;
}

/**
 * Return true if `rawName` is empty, confidential, or a non-owner placeholder (`DF-1`, `DF-4`).
 * Synchronized 1:1 with `is_placeholder_owner_token` in `pipeline/atlas_pipeline/schema.py`.
 */
export function isPlaceholderOwnerName(rawName = "") {
  const raw = String(rawName || "").trim();
  if (!raw) return true;
  if (isConfidentialOwnerName(raw)) return true;
  const cleaned = raw
    .replace(OWNER_CARE_OF_TAIL_RE, "")
    .replace(/[^A-Za-z0-9\s]+/g, " ")
    .toUpperCase()
    .replace(/\s+/g, " ")
    .trim();
  if (!cleaned) return true;
  return PLACEHOLDER_OWNER_TOKENS.has(cleaned);
}

/**
 * Return true if `rawName` represents a corporate, institutional, civic, or trust owner (`ENTITY_KEYWORD_RE` + `TRUST_ENTITY_RE`).
 */
export function isEntityOrTrustOwnerName(rawName = "") {
  const orig = String(rawName || "").trim();
  if (!orig || isPlaceholderOwnerName(orig)) return false;
  const upperOrig = orig
    .replace(OWNER_CARE_OF_TAIL_RE, "")
    .replace(OWNER_DBA_TAIL_RE, "")
    .toUpperCase()
    .trim();
  if (!upperOrig) return false;
  const collapsedAbbrev = upperOrig.replace(
    /\b([A-Z])\.([A-Z])\.(?:([A-Z])\.)?(?:([A-Z])\.)?/g,
    (_, a, b, c, d) => `${a}${b}${c || ""}${d || ""}`
  );
  const looksLikeSpouseHoaGivenName =
    /\s+(?:&|AND)\s+HOA(?:\s+[A-Z]+|\s*$)/i.test(collapsedAbbrev) &&
    !/\b(?:ASSOC|ASSN|ASSOCIATION|CONDO|CONDOMINIUM|TOWNHOMES|VILLAS|ESTATES|POA|INC|LLC|PLLC|CORP)\b/i.test(
      collapsedAbbrev
    );
  if (ENTITY_KEYWORD_RE.test(collapsedAbbrev) && !looksLikeSpouseHoaGivenName) {
    return true;
  }
  return TRUST_ENTITY_RE.test(collapsedAbbrev);
}

const NON_SUBDIVISION_LINE_START_RE =
  /^(?:(?:STATE\s+CLASS|GRADE|LTS?|LOTS?|TRS?|TRACTS?|TRA|RES|RESERVE|BLKS?|BLOCKS?|SEC|SECT|SECTION|UNITS?|BLDG|BUILDING|ABST|ABSTRACT|O\/L|OUTLOT|OUT\s+LOT|PARCEL|CONDO|GARAGE|PARKING|SPACE|SLIP|ESMT|EASEMENT)\b|(?:N|S|E|W|NE|NW|SE|SW|NO|SO|EA|WE|NORTH|SOUTH|EAST|WEST|PT|PART|POR|PORTION|ALL|REM|REMAINDER)\s+(?:OF\s+|\d)|OF\s+(?:LTS?|LOTS?|TRS?|TRACTS?|BLKS?|BLOCKS?|SEC|SECT|RES)\b|\d+\/\d+\s+(?:OF\s+|\b(?:LTS?|LOTS?|TRS?|TRACTS?|BLKS?|BLOCKS?)\b)|\d+(?:\.\d+)?\s*(?:FT|FEET|X|AC|ACRES|SQ\s*FT|SF|%)\b|\d+(?:\.\d+)?X\d+|\()/i;

const PURE_MODIFIER_LINE_RE =
  /^(?:U\/R|UNRECORDED|R\/P|PAR\s+R\/P|PARTIAL\s+REPLAT|REPLAT|AMENDED|AMD|\([^)]+\)|\d+(?:\.\d+)?%\s*INT.*)$/i;

/**
 * Split raw HCAD legal_lines or middot-joined legalDescription into clean line segments.
 */
export function splitHcadLegalLines(legalLinesOrDesc) {
  if (Array.isArray(legalLinesOrDesc)) {
    return legalLinesOrDesc
      .map((s) => String(s || "").trim())
      .filter(Boolean);
  }
  const raw = String(legalLinesOrDesc || "").trim();
  if (!raw) return [];
  if (raw.includes("|")) {
    return raw
      .split("|")
      .map((s) => s.trim())
      .filter(Boolean);
  }
  if (raw.includes("·")) {
    return raw
      .split("·")
      .map((s) => s.trim())
      .filter(Boolean);
  }
  return [raw];
}

/**
 * Identify the true recorded plat / addition segment from HCAD's 1-4 legal lines
 * (lgl_1..lgl_4), skipping Lot/Block/Tract/Abstract/dimension lines.
 */
export function extractLegalSubdivisionFromLines(legalLinesOrDesc) {
  const lines = splitHcadLegalLines(legalLinesOrDesc);
  if (lines.length === 0) return "";

  const isCandidatePlatLine = (line) => {
    const s = String(line || "").trim();
    if (!s || s.length < 2) return false;
    if (PURE_MODIFIER_LINE_RE.test(s)) return false;
    if (NON_SUBDIVISION_LINE_START_RE.test(s)) return false;
    if (/\b\d+(?:\.\d+)?\s*X\s*\d+(?:\.\d+)?\b/i.test(s)) return false;
    if (!/[A-Z]{2,}/i.test(s)) return false;
    return true;
  };

  // Prefer lines 2..4 (indices 1..n-1) where HCAD stores recorded subdivision / addition names
  for (let i = 1; i < lines.length; i += 1) {
    if (isCandidatePlatLine(lines[i])) {
      return lines[i];
    }
  }
  // If single-line legal description is itself a plat/addition name (without leading LT/TR/BLK/ABST)
  if (lines.length === 1 && isCandidatePlatLine(lines[0])) {
    return lines[0];
  }
  return "";
}

/**
 * Normalize an HCAD or historic plat name into the exact string expected by
 * Harris County Clerk RP.aspx "Subdivision or Description" (txtDesc).
 * Examples:
 *   "North Norhill Addition"     -> "NORTH NORHILL"
 *   "Fairview Addition"          -> "FAIRVIEW"
 *   "DEPENBROCK ALLEN SEC 62"    -> "DEPENBROCK ALLEN"
 *   "SSBB PAR R/P"               -> "SSBB"
 *   "HOUSTON HEIGHTS R/P"        -> "HOUSTON HEIGHTS"
 */
export function cleanClerkSubdivisionDesc(rawName = "") {
  let s = String(rawName || "").trim();
  if (!s) return "";

  // Remove parenthetical notes such as "(NM)" or "(c. 1893 Historic Plat)"
  s = s.replace(/\([^)]*\)/g, " ");

  // Strip trailing replat, section, phase, and unrecorded qualifiers
  s = s.replace(
    /\b(?:PAR(?:TIAL)?\s+R\/P|PARTIAL\s+REPLAT|R\/P|REPLAT|AMENDED|AMD|U\/R|UNRECORDED)\b.*$/i,
    " "
  );
  s = s.replace(
    /\s+\b(?:SEC|SECT|SECTION|PH|PHASE)\s+[0-9A-Z\-]+.*$/i,
    " "
  );

  s = s.replace(/\s+/g, " ").trim();

  // Strip trailing "ADDITION" or "SUBDIVISION" only when a meaningful root name precedes it
  const strippedSuffix = s
    .replace(/\s+\b(?:ADDITION|SUBDIVISION|SUBD)\b\.?$/i, "")
    .trim();
  if (strippedSuffix.length >= 3) {
    s = strippedSuffix;
  }

  return s.toUpperCase();
}

function normalizeAlnumStem(s = "") {
  return cleanClerkSubdivisionDesc(s).replace(/[^A-Z0-9]+/g, "");
}

function parsePrimaryAndDisplayToken(rawSpan = "") {
  const cleaned = String(rawSpan || "")
    .trim()
    .replace(/\s+/g, " ");
  if (!cleaned) return { primary: "", display: "" };
  const firstMatch = cleaned.match(/^([0-9]+[A-Z]?|[A-Z][0-9]*)/i);
  const primary = firstMatch ? firstMatch[1].toUpperCase() : cleaned.split(/[\s,&]+/)[0].toUpperCase();
  const hasMultiple =
    /[&,]|\bTHRU\b|\bTHROUGH\b|\bTO\b|\s+/i.test(cleaned) &&
    cleaned.toUpperCase() !== primary;
  return {
    primary,
    display: hasMultiple ? `${primary} (of ${cleaned.toUpperCase()})` : primary,
  };
}

/**
 * Parse Root Plat Deed/Map Records Volume & Page or Clerk Instrument number from
 * reconstructed historic plat citations.
 */
export function parseRootPlatCitation(citation = "") {
  const raw = String(citation || "").trim();
  if (!raw) {
    return {
      series: "",
      volume: "",
      page: "",
      instrument: "",
      summary: "",
    };
  }
  const primaryPart = raw
    .replace(/^Harris\s+County\s+/i, "")
    .split("·")[0]
    .trim();

  let series = "";
  if (/\bDeed\s+Records\b/i.test(primaryPart)) {
    series = "Deed Records (DR)";
  } else if (/\bMap\s+Records\b/i.test(primaryPart)) {
    series = "Map Records (MR)";
  }

  let volume = "";
  let page = "";
  const vpMatch = primaryPart.match(
    /\bVol(?:ume)?\.?\s*([0-9A-Z]+)\s*,\s*Pg\.?\s*([0-9A-Z]+)/i
  );
  if (vpMatch) {
    volume = vpMatch[1].toUpperCase();
    page = vpMatch[2].toUpperCase();
  }

  let instrument = "";
  const instMatch = primaryPart.match(
    /\b(?:Instrument|File|Clerk)\s*#\s*([0-9A-Z\-]+)/i
  );
  if (instMatch) {
    instrument = instMatch[1].toUpperCase();
  }

  return {
    series,
    volume,
    page,
    instrument,
    summary: primaryPart,
  };
}

/**
 * Extract all 8 Harris County Clerk Real Property (RP.aspx) legal description tokens.
 */
export function parseClerkLegalTokens(input = {}, legacySubdivision = "") {
  const opts =
    typeof input === "string"
      ? { legalDescription: input, subdivision: legacySubdivision }
      : input || {};

  const lines = splitHcadLegalLines(opts.legalLines || opts.legalDescription || "");
  const rawLegal = lines.join(" | ");
  const legalSubFromLines =
    String(opts.legalSubdivision || "").trim() ||
    extractLegalSubdivisionFromLines(lines);

  const rawSub = String(opts.subdivision || "").trim();
  const marketNbhd = String(opts.marketNeighborhood || "").trim();
  const historicParentPlat = opts.historicParentPlat || null;
  const subProps = opts.subProps || null;

  // Choose recorded plat name:
  // 1. Legal plat segment from legal_lines (e.g. "NORTH NORHILL", "SSBB") ALWAYS wins over marketNeighborhood ("NORHILL")
  // 2. Otherwise rawSub if it isn't merely marketNeighborhood shadowing historicParentPlat
  let chosenAdditionRaw = legalSubFromLines;
  if (!chosenAdditionRaw && rawSub) {
    const normRawSub = normalizeAlnumStem(rawSub);
    const normMarket = normalizeAlnumStem(marketNbhd);
    const parentRaw = String(
      historicParentPlat?.name || historicParentPlat?.full_name || ""
    ).trim();
    const normParent = normalizeAlnumStem(parentRaw);
    if (normMarket && normRawSub === normMarket && normParent && normParent !== normMarket) {
      chosenAdditionRaw = parentRaw;
    } else {
      chosenAdditionRaw = rawSub;
    }
  }
  if (!chosenAdditionRaw && historicParentPlat) {
    chosenAdditionRaw = String(
      historicParentPlat.name || historicParentPlat.full_name || ""
    ).trim();
  }

  let lot = "";
  let lotDisplay = "";
  let tract = "";
  let tractDisplay = "";
  let block = "";
  let section = "";
  let unit = "";
  let reserve = "";
  let abstractCode = "";
  let abstractSurvey = "";
  let outlot = "";

  // Structured lot/tract identifier span supporting numeric/alphanumeric (14, 3A, 4A-1)
  // and single-letter (B, A, E & F) IDs without swallowing multi-letter townsite names or compass calls
  const COMPASS_DIM_LOOKAHEAD =
    "(?=[NSEW]\\b[\\s|]+(?:\\d+(?:\\.\\d+)?(?:\\s*(?:FT|FEET|X|AC|ACRES|')|X\\d+|\\/\\d+)|OF\\b|HLF\\b|HALF\\b))";
  const ID_TOKEN = `(?:\\d+[A-Z]?(?:-\\d+[A-Z]?)?\\b|(?!${COMPASS_DIM_LOOKAHEAD})[A-Z](?:\\d+[A-Z]?)?(?:-\\d+[A-Z]?)?\\b)`;
  const ID_SEP = "(?:\\s*(?:[&,]\\s*|\\b(?:THRU|THROUGH|TO)\\b\\s*)|\\s+)";
  const ID_SPAN = `(${ID_TOKEN}(?:${ID_SEP}${ID_TOKEN})*)`;

  if (rawLegal) {
    // 1. Lot (ONLY LT / LTS / LOT / LOTS, never Tract!)
    const lotRe = new RegExp(`\\b(?:LTS?|LOTS?)\\s+${ID_SPAN}`, "i");
    const lotMatch = rawLegal.match(lotRe);
    if (lotMatch && lotMatch[1]) {
      const parsed = parsePrimaryAndDisplayToken(lotMatch[1]);
      lot = parsed.primary;
      lotDisplay = parsed.display;
    }

    // 2. Tract (ONLY TR / TRS / TRA / TRACT / TRACTS, separate Clerk box!)
    const tractRe = new RegExp(`\\b(?:TRS?|TRA|TRACTS?)\\s+${ID_SPAN}`, "i");
    const tractMatch = rawLegal.match(tractRe);
    if (tractMatch && tractMatch[1]) {
      const parsed = parsePrimaryAndDisplayToken(tractMatch[1]);
      tract = parsed.primary;
      tractDisplay = parsed.display;
    }

    // 3. Block
    const blkMatch = rawLegal.match(/\b(?:BLKS?|BLOCKS?)\s+([0-9A-Z\-]+)/i);
    if (blkMatch && blkMatch[1]) {
      block = blkMatch[1].trim().toUpperCase();
    }

    // 4. Section
    const secMatch = rawLegal.match(/\b(?:SEC|SECT|SECTION)\s+([0-9A-Z\-]+)/i);
    if (secMatch && secMatch[1]) {
      section = secMatch[1].trim().toUpperCase();
    }

    // 5. Unit
    const unitMatch = rawLegal.match(/\bUNIT\s+([0-9A-Z\-]+)/i);
    if (unitMatch && unitMatch[1]) {
      unit = unitMatch[1].trim().toUpperCase();
    }

    // 6. Reserve
    const resMatch = rawLegal.match(
      /\b(?:RES|RESERVE)\s+([A-Z0-9][A-Z0-9\-]{0,5})(?=\s|$|\|)/i
    );
    if (resMatch && resMatch[1] && !/^(?:RPRS|REPAIRS)$/i.test(resMatch[1])) {
      reserve = resMatch[1].trim().toUpperCase();
    }

    // 7. Abstract (numeric Texas GLO Abstract # + optional original survey grantee)
    const abstMatch = rawLegal.match(
      /\b(?:ABST|ABSTRACT)\s*#?\s*(\d{1,4})(?:\s+([A-Z][A-Z0-9\s\.&]{1,30}?))?(?=\s*\||$|\s+(?:LTS?|BLKS?|TRS?|SEC)\b)/i
    );
    if (abstMatch && abstMatch[1]) {
      abstractCode = String(Number(abstMatch[1]));
      abstractSurvey = String(abstMatch[2] || "").trim().replace(/\s+/g, " ");
    }

    // 8. Outlot (e.g. "O/L 62" or "OUTLOT 14")
    const outlotMatch = rawLegal.match(
      /\b(?:O\/L|OUTLOT|OUT\s+LOT)\s*#?\s*([0-9A-Z\-]+)/i
    );
    if (outlotMatch && outlotMatch[1]) {
      outlot = outlotMatch[1].trim().toUpperCase();
    }
  }

  // Fallback section extraction from subdivision name (e.g. "DEPENBROCK ALLEN SEC 62")
  if (!section && chosenAdditionRaw) {
    const subSecMatch = chosenAdditionRaw.match(/\b(?:SEC|SECT|SECTION)\s+([0-9A-Z\-]+)/i);
    if (subSecMatch && subSecMatch[1]) {
      section = subSecMatch[1].trim().toUpperCase();
    }
  }
  if (!section && rawSub) {
    const rawSubSec = rawSub.match(/\b(?:SEC|SECT|SECTION)\s+([0-9A-Z\-]+)/i);
    if (rawSubSec && rawSubSec[1]) {
      section = rawSubSec[1].trim().toUpperCase();
    }
  }

  const subdivisionDesc = cleanClerkSubdivisionDesc(chosenAdditionRaw);
  const historicParentDesc = historicParentPlat
    ? cleanClerkSubdivisionDesc(
        historicParentPlat.name || historicParentPlat.full_name || ""
      )
    : "";

  const normSubDesc = subdivisionDesc.replace(/[^A-Z0-9]+/g, "");
  const normHistDesc = historicParentDesc.replace(/[^A-Z0-9]+/g, "");
  const hasDistinctHistoricParent = Boolean(
    historicParentDesc &&
      normHistDesc &&
      normSubDesc &&
      normHistDesc !== normSubDesc
  );

  const rootCitation = parseRootPlatCitation(
    historicParentPlat?.deed_citation ||
      historicParentPlat?.plat_citation ||
      subProps?.deed_citation ||
      subProps?.plat_citation ||
      ""
  );

  return {
    subdivisionDesc,
    historicParentDesc: hasDistinctHistoricParent ? historicParentDesc : "",
    hasDistinctHistoricParent,
    section,
    block,
    lot,
    lotDisplay: lotDisplay || lot,
    tract,
    tractDisplay: tractDisplay || tract,
    reserve,
    unit,
    abstract: abstractCode,
    abstractSurvey,
    outlot,
    rootPlatCitation: rootCitation,
    // Backwards-compatible aliases used by printable property sheet
    addition: chosenAdditionRaw || "",
    cleanAdditionSearch: subdivisionDesc || historicParentDesc || "",
    rawLegal,
    source: rawLegal || chosenAdditionRaw,
  };
}

/**
 * Sanitize an HCAD owner string into 1-click Grantee / Grantor search tokens
 * formatted for Harris County Clerk RP.aspx ("Last Name First - No Punctuation").
 *
 * Examples:
 *   "MORRIS LAURA M & DAVID V" ->
 *     [{ label: "Grantee", copy: "MORRIS LAURA", display: "MORRIS LAURA" },
 *      { label: "Grantee (2)", copy: "MORRIS DAVID", display: "MORRIS DAVID" }]
 *   "ABC HOLDINGS, L.L.C." ->
 *     [{ label: "Grantee", copy: "ABC HOLDINGS LLC", display: "ABC HOLDINGS LLC" }]
 */
export function formatClerkPartyTokens(rawOwner = "") {
  const orig = String(rawOwner || "").trim();
  if (!orig || isPlaceholderOwnerName(orig)) return [];
  // Strip HCAD care-of ("% AGENT NAME" / "C/O ...") and trailing "(DBA ...)" suffix before tokenizing (`DF-2`)
  const upperOrig = orig
    .replace(OWNER_CARE_OF_TAIL_RE, "")
    .replace(OWNER_DBA_TAIL_RE, "")
    .toUpperCase()
    .trim();
  if (!upperOrig || isPlaceholderOwnerName(upperOrig)) {
    return [];
  }

  // Collapse dotted abbreviations like "L.L.C." -> "LLC", "P.L.L.C." -> "PLLC", and "L.P." -> "LP" before punctuation removal
  const collapsedAbbrev = upperOrig.replace(
    /\b([A-Z])\.([A-Z])\.(?:([A-Z])\.)?(?:([A-Z])\.)?/g,
    (_, a, b, c, d) => `${a}${b}${c || ""}${d || ""}`
  );

  // Guard Vietnamese spouse given name "HOA" after "&" (e.g. "LE HUNG V & HOA T") from matching HOA entity keyword
  const looksLikeSpouseHoaGivenName =
    /\s+(?:&|AND)\s+HOA(?:\s+[A-Z]+|\s*$)/i.test(collapsedAbbrev) &&
    !/\b(?:ASSOC|ASSN|ASSOCIATION|CONDO|CONDOMINIUM|TOWNHOMES|VILLAS|ESTATES|POA|INC|LLC|CORP)\b/i.test(
      collapsedAbbrev
    );

  // Check if corporate, municipal, institutional, or commercial entity
  if (ENTITY_KEYWORD_RE.test(collapsedAbbrev) && !looksLikeSpouseHoaGivenName) {
    const cleanEntity = collapsedAbbrev
      .replace(/&/g, " ")
      .replace(/[^A-Z0-9\s]+/g, " ")
      .replace(/\bL\s+L\s+C\b/g, "LLC")
      .replace(/\s+/g, " ")
      .trim();
    return cleanEntity
      ? [{ label: "Grantee", copy: cleanEntity, display: cleanEntity }]
      : [];
  }

  // Individual / family co-owners: split on "&" or " AND " before stripping "&"
  const rawSegments = collapsedAbbrev
    .split(/\s+(?:&|AND)\s+/)
    .map((seg) =>
      seg
        .replace(BOILERPLATE_TRAILING_RE, " ")
        .replace(/^([A-Z0-9'-]+\s+[A-Z0-9'-]+(?:\s+[A-Z0-9'-]+)*)\s+L\s+E$/i, "$1")
        .replace(/[^A-Z0-9\s]+/g, " ")
        .replace(/\s+/g, " ")
        .trim()
    )
    .filter(Boolean);

  if (rawSegments.length === 0) return [];

  const stripSuffixes = (words) =>
    words.filter((w) => !NAME_SUFFIX_TOKENS.has(w) && !/^\d{2,4}$/.test(w));

  const seg0Words = stripSuffixes(rawSegments[0].split(" ").filter(Boolean));
  if (seg0Words.length === 0) return [];

  const primarySurname = seg0Words[0];
  const results = [];
  const seen = new Set();

  const pushUnique = (val) => {
    const clean = String(val || "").replace(/\s+/g, " ").trim();
    if (!clean || seen.has(clean)) return;
    seen.add(clean);
    results.push({
      label: results.length === 0 ? "Grantee" : `Grantee (${results.length + 1})`,
      copy: clean,
      display: clean,
    });
  };

  // Primary owner: "SURNAME GIVEN" (or "SURNAME INITIAL1 INITIAL2" for initial-only names like "MOORE W I")
  if (seg0Words.length === 1) {
    pushUnique(primarySurname);
  } else if (seg0Words[1].length === 1 && seg0Words.length >= 3 && seg0Words[2].length === 1) {
    pushUnique(`${primarySurname} ${seg0Words[1]} ${seg0Words[2]}`);
  } else {
    pushUnique(`${primarySurname} ${seg0Words[1]}`);
  }

  // Co-owners after "&" (shared surname "SURNAME GIVEN1 & GIVEN2" vs distinct surname "SURNAME1 GIVEN1 M1 & SURNAME2 GIVEN2 M2")
  for (let i = 1; i < rawSegments.length; i += 1) {
    const words = stripSuffixes(rawSegments[i].split(" ").filter(Boolean));
    if (words.length === 0) continue;
    if (words[0] === primarySurname && words.length >= 2) {
      pushUnique(`${primarySurname} ${words[1]}`);
    } else if (words.length >= 3 && words[0].length > 1) {
      if (words[1].length === 1 && words[2].length === 1) {
        pushUnique(`${words[0]} ${words[1]} ${words[2]}`);
      } else {
        pushUnique(`${words[0]} ${words[1]}`);
      }
    } else if (words[0].length === 1 && words.length >= 2 && words[1].length === 1) {
      pushUnique(`${primarySurname} ${words[0]} ${words[1]}`);
    } else {
      pushUnique(`${primarySurname} ${words[0]}`);
    }
  }

  return results;
}

/**
 * Normalize a single compact HCAD Clerk instrument token (`FC:...`, `PR:...`, `VP:vol/pg`,
 * `R20XX-NNNNN`, or numeric Clerk File Number) into a canonical lookup object (`DF-6`, `MOD-2`).
 */
export function formatClerkInstrumentToken(rawToken = "", dateDisplay = "") {
  const token = String(rawToken || "").trim();
  if (!token) return null;

  if (token.startsWith("FC:")) {
    const filmCode = token.slice(3).trim();
    if (!filmCode) return null;
    return {
      date: dateDisplay,
      kind: "film_code",
      clerkBoxLabel: "Film Code",
      copyValue: filmCode,
      displayValue: filmCode,
      portal: "rp",
      matchKey: `FC:${filmCode.toUpperCase()}`,
    };
  }
  if (token.startsWith("PR:")) {
    const caseNo = token.slice(3).trim();
    if (!caseNo) return null;
    return {
      date: dateDisplay,
      kind: "probate",
      clerkBoxLabel: "Probate Case #",
      copyValue: caseNo,
      displayValue: caseNo,
      portal: "probate",
      matchKey: `PR:${caseNo.toUpperCase()}`,
    };
  }
  if (token.startsWith("VP:")) {
    const [volRaw = "", pgRaw = ""] = token.slice(3).split("/");
    const vol = volRaw.trim();
    const pg = pgRaw.trim();
    if (!vol && !pg) return null;
    return {
      date: dateDisplay,
      kind: "vol_page",
      clerkBoxLabel: "Vol / Page",
      copyValue: vol,
      pageValue: pg,
      displayValue: `Vol ${vol}, Pg ${pg}`,
      portal: "archives",
      matchKey: `VP:${vol.toUpperCase()}/${pg.toUpperCase()}`,
    };
  }

  const fileNumber = /^R20\d{2}-\d+$/i.test(token)
    ? `RP-${token.slice(1).toUpperCase()}`
    : token.toUpperCase();
  return {
    date: dateDisplay,
    kind: "file_number",
    clerkBoxLabel: "File Number",
    copyValue: fileNumber,
    displayValue: fileNumber,
    portal: "rp",
    matchKey: `FN:${fileNumber}`,
  };
}

/**
 * Canonical parser for compact deed wire strings ("20190329:R2019-149789|20120820:20120430232|...",
 * 4-part "20190415:R2019-149789:1:D", or TPIA 6-part "19940615:P812345::D:GRANTOR:GRANTEE") stored in
 * permits_<prefix3>.json.gz["<hcad_num>"].d.
 *
 * Suppresses undated HCAD sentinel dates ("00000000") (`isoDate = ""`) and reassembles
 * prefixed Clerk tokens (`FC:...`, `PR:...`, `VP:...`) in one shared place.
 */
export function parseCompactDeedWireEntries(compactStr = "") {
  const raw = String(compactStr || "").trim();
  if (!raw) return [];
  const out = [];
  for (const entry of raw.split("|")) {
    const cleanEntry = entry.trim();
    if (!cleanEntry) continue;
    const colonIdx = cleanEntry.indexOf(":");
    if (colonIdx <= 0) continue;
    const dt8 = cleanEntry.slice(0, colonIdx).trim();
    const restParts = cleanEntry.slice(colonIdx + 1).split(":");
    const isPrefixTok = restParts.length >= 2 && /^(?:FC|PR|VP)$/i.test(restParts[0].trim());
    const token = isPrefixTok
      ? `${restParts[0].trim()}:${restParts[1].trim()}`
      : String(restParts[0] || "").trim();
    const extraOffset = isPrefixTok ? 2 : 1;
    const deedId = String(restParts[extraOffset] || "").trim();
    const typeCode = String(restParts[extraOffset + 1] || "D").trim();
    const grantor = String(restParts[extraOffset + 2] || "").trim();
    const grantee = String(restParts[extraOffset + 3] || "").trim();
    let isoDate = "";
    if (/^\d{8}$/.test(dt8) && dt8 !== "00000000") {
      const yyyy = dt8.slice(0, 4);
      const mm = dt8.slice(4, 6);
      const dd = dt8.slice(6, 8);
      isoDate = mm === "00" || dd === "00" ? yyyy : `${yyyy}-${mm}-${dd}`;
    } else if (!/^\d{8}$/.test(dt8)) {
      isoDate = dt8;
    }
    out.push({ dt8, isoDate, token, deedId, typeCode, grantor, grantee });
  }
  return out;
}

/**
 * Decode a compact deed chain string ("20190329:R2019-149789|20120820:20120430232|...",
 * 4-part "20190415:R2019-149789:1:D", or TPIA 6-part "19940615:P812345::D:GRANTOR:GRANTEE")
 * stored in permits_<prefix3>.json.gz["<hcad_num>"].d into structured Clerk lookup items.
 */
export function decodeCompactDeedChain(compactStr = "") {
  const items = [];
  for (const { isoDate, token, grantor, grantee } of parseCompactDeedWireEntries(compactStr)) {
    if (!token) continue;
    const formatted = formatClerkInstrumentToken(token, isoDate);
    if (formatted) {
      if (grantor) formatted.grantor = grantor;
      if (grantee) formatted.grantee = grantee;
      items.push(formatted);
    }
  }
  return items;
}

/**
 * Decode compact TPIA Mechanic's Lien / Builder Contract wire strings (`ml`:
 * "<dt8>~<tok>~<contractor>~<owner>~<amount>|...") from `permits_<prefix3>.json.gz`.
 */
export function decodeCompactMechanicsLiens(rawMl = "") {
  const raw = String(rawMl || "").trim();
  if (!raw) return [];
  const items = [];
  for (const seg of raw.split("|")) {
    const clean = seg.trim();
    if (!clean) continue;
    const parts = clean.split("~");
    if (parts.length < 3) continue;
    const dt8 = String(parts[0] || "").trim();
    const token = String(parts[1] || "").trim();
    const contractor = String(parts[2] || "").trim();
    const ownerName = String(parts[3] || "").trim();
    const amount = String(parts[4] || "").trim();
    if (!token || !contractor) continue;
    let isoDate = "";
    if (/^\d{8}$/.test(dt8) && dt8 !== "00000000") {
      const yyyy = dt8.slice(0, 4);
      const mm = dt8.slice(4, 6);
      const dd = dt8.slice(6, 8);
      isoDate = mm === "00" || dd === "00" ? yyyy : `${yyyy}-${mm}-${dd}`;
    } else if (dt8 && dt8 !== "00000000") {
      isoDate = dt8;
    }
    const instrument = formatClerkInstrumentToken(token, isoDate);
    if (!instrument) continue;
    items.push({
      dt8,
      date: isoDate,
      token,
      contractor,
      ownerName,
      amount,
      instrument,
    });
  }
  return items;
}

/**
 * Decode compact TPIA Deed of Trust institutional lender wire strings (`dt_lender`:
 * "<dt8>~<tok>~<lender>|...") from `permits_<prefix3>.json.gz`.
 */
export function decodeCompactDeedOfTrustLenders(rawDtLender = "") {
  const raw = String(rawDtLender || "").trim();
  if (!raw) return [];
  const items = [];
  for (const seg of raw.split("|")) {
    const clean = seg.trim();
    if (!clean) continue;
    const parts = clean.split("~");
    if (parts.length < 3) continue;
    const dt8 = String(parts[0] || "").trim();
    const token = String(parts[1] || "").trim();
    const lender = parts.slice(2).join("~").trim();
    if (!token || !lender) continue;
    let isoDate = "";
    if (/^\d{8}$/.test(dt8) && dt8 !== "00000000") {
      const yyyy = dt8.slice(0, 4);
      const mm = dt8.slice(4, 6);
      const dd = dt8.slice(6, 8);
      isoDate = mm === "00" || dd === "00" ? yyyy : `${yyyy}-${mm}-${dd}`;
    } else if (dt8 && dt8 !== "00000000") {
      isoDate = dt8;
    }
    const instrument = formatClerkInstrumentToken(token, isoDate);
    if (!instrument) continue;
    items.push({
      dt8,
      date: isoDate,
      token,
      lender,
      instrument,
    });
  }
  return items;
}

/**
 * Shared dual-mode decompressor for RFC 1952 `.json.gz` static shards (`owners_`, `permits_`, `addr_`).
 *
 * Why dual-mode magic-byte inspection is required:
 *   - Local `app/server.py` serves `.json.gz` with `Content-Type: application/gzip` and NO
 *     `Content-Encoding` header, so `fetch().arrayBuffer()` delivers raw RFC 1952 gzip bytes (`0x1f 0x8b`).
 *   - Static CDNs (GitHub Pages / Fastly) or reverse proxies may transparently attach
 *     `Content-Encoding: gzip` to `.gz` assets, causing the browser's HTTP stack to gunzip at
 *     the transport layer and deliver plain UTF-8 JSON (`0x7b` `{`) to `arrayBuffer()`.
 *   - Uses standard Web Streams `DecompressionStream("gzip")` across both browser and Node 22+
 *     so unit tests exercise the exact production codepath.
 */
export async function decompressGzipJsonObjectBytes(
  inputBytes,
  shardLabel = "Gzipped JSON shard"
) {
  const bytes =
    inputBytes instanceof Uint8Array
      ? inputBytes
      : inputBytes instanceof ArrayBuffer
      ? new Uint8Array(inputBytes)
      : ArrayBuffer.isView(inputBytes)
      ? new Uint8Array(inputBytes.buffer, inputBytes.byteOffset, inputBytes.byteLength)
      : null;

  if (!bytes || bytes.byteLength === 0) {
    throw new Error(`${shardLabel} payload is empty.`);
  }

  let jsonText = "";
  if (bytes.byteLength >= 2 && bytes[0] === 0x1f && bytes[1] === 0x8b) {
    if (typeof DecompressionStream !== "function") {
      throw new Error("DecompressionStream('gzip') is unavailable in this runtime.");
    }
    try {
      const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
      jsonText = await new Response(stream).text();
    } catch (err) {
      throw new Error(
        `Failed to gunzip ${shardLabel.toLowerCase()}: ${
          err instanceof Error ? err.message : String(err)
        }`
      );
    }
  } else {
    let offset = 0;
    // Skip optional UTF-8 BOM (`0xEF 0xBB 0xBF`)
    if (
      bytes.byteLength >= 3 &&
      bytes[0] === 0xef &&
      bytes[1] === 0xbb &&
      bytes[2] === 0xbf
    ) {
      offset = 3;
    }
    while (
      offset < bytes.byteLength &&
      (bytes[offset] === 0x20 ||
        bytes[offset] === 0x09 ||
        bytes[offset] === 0x0a ||
        bytes[offset] === 0x0d)
    ) {
      offset += 1;
    }
    if (offset >= bytes.byteLength) {
      throw new Error(`${shardLabel} contains only whitespace.`);
    }
    if (bytes[offset] === 0x3c) {
      throw new Error(`${shardLabel} returned HTML (<) instead of gzip/JSON.`);
    }
    if (bytes[offset] !== 0x7b) {
      throw new Error(
        `Unexpected leading byte 0x${bytes[offset].toString(16).padStart(2, "0")} in ${shardLabel.toLowerCase()}.`
      );
    }
    jsonText = new TextDecoder("utf-8", { fatal: true }).decode(bytes.subarray(offset));
  }

  let parsed;
  try {
    parsed = JSON.parse(jsonText);
  } catch (err) {
    throw new Error(
      `Invalid JSON in ${shardLabel.toLowerCase()}: ${
        err instanceof Error ? err.message : String(err)
      }`
    );
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`${shardLabel} root must be a plain JSON object.`);
  }
  return parsed;
}

export async function decompressOwnerHistoryBytes(inputBytes) {
  return decompressGzipJsonObjectBytes(inputBytes, "Owner history shard");
}

export async function decompressPermitShardBytes(inputBytes) {
  return decompressGzipJsonObjectBytes(inputBytes, "Permit shard");
}

export async function decompressAddressShardBytes(inputBytes) {
  return decompressGzipJsonObjectBytes(inputBytes, "Address shard");
}

/**
 * Decode a `fetch()` Response for a `.json.gz` shard using `decompressFn` when `res.arrayBuffer`
 * is present, falling back to `res.json()` strictly for lightweight `{ ok: true, json }` unit-test stubs.
 */
export async function decodeGzipJsonResponse(res, decompressFn = decompressGzipJsonObjectBytes) {
  if (!res || !res.ok) return {};
  if (typeof res.arrayBuffer === "function") {
    const buf = await res.arrayBuffer();
    return await decompressFn(buf);
  }
  if (typeof res.json === "function") {
    const obj = await res.json();
    return obj && typeof obj === "object" && !Array.isArray(obj) ? obj : {};
  }
  return {};
}

/**
 * Normalize a situs house-number prefix into a canonical 2-digit `addr_<10..99>.json.gz` shard key,
 * right-padding 1-digit numbers (e.g. "5" -> "50"), rejecting "00".."09", and short-circuiting
 * prefixes absent from `pmtiles_manifest.json` `address_shard_prefixes`.
 */
export function normalizeAddressShardPrefix(shardKey, knownPrefixes = null) {
  const key = String(shardKey || "").trim().padEnd(2, "0").slice(0, 2);
  if (!/^[1-9][0-9]$/.test(key)) return "";
  if (Array.isArray(knownPrefixes) && !knownPrefixes.includes(key)) return "";
  return key;
}

/**
 * Build the versioned `.json.gz` URL for a normalized 2-digit address shard key.
 */
export function buildAddressShardUrl(key, cacheBust = "") {
  const cb = String(cacheBust || "").trim();
  return cb
    ? `public/data/address_shards/addr_${encodeURIComponent(key)}.json.gz?v=${encodeURIComponent(cb)}`
    : `public/data/address_shards/addr_${encodeURIComponent(key)}.json.gz?v=20261113c`;
}

/**
 * Decode a compact 22-year HCAD certified-roll ownership chain string (`DF-4`, `ANL-2`).
 *
 * Compact Wire Grammar (1:1 synchronized with `encode_compact_owner_history` in
 * `pipeline/atlas_pipeline/build_historical_ownership_2005_2026.py`):
 *   - Record string: `<tenure_0>|<tenure_1>|...|<tenure_k>` ordered newest-first (`k < 12`).
 *   - Each `<tenure_i>` has 4 `~`-delimited fields: `<span_yr>~<acq_dt>~<deed_tok>~<owner_name>`
 *     1. `<span_yr>`   : `"YYYY"` (single roll year) or `"YYYY-YYYY"` (contiguous roll span).
 *     2. `<acq_dt>`    : `"YYYYMMDD"`, `"YYYY"`, `"<=1988"` (HCAD `1988-01-02` mainframe baseline), or `""`.
 *     3. `<deed_tok>`  : Paired Clerk instrument token (`R20XX-NNNNN`, numeric file #, `FC:...`,
 *                        `PR:...`, `VP:vol/pg`) or `""`.
 *     4. `<owner_name>`: Sanitized HCAD roll owner name (rest-joined on `~` for defensive tolerance).
 */
export function decodeCompactOwnerHistory(compactOhStr = "", currentOwnerName = "") {
  if (isConfidentialOwnerName(currentOwnerName)) return [];
  const raw = String(compactOhStr || "").trim();
  if (!raw) return [];

  const tenures = [];
  const entries = raw.split("|");
  for (let i = 0; i < entries.length; i += 1) {
    const entry = entries[i].trim();
    if (!entry) continue;
    const parts = entry.split("~");
    if (parts.length < 4) continue;

    const spanRaw = parts[0].trim();
    const acqRaw = parts[1].trim();
    const deedRaw = parts[2].trim();
    let ownerName = parts.slice(3).join("~").trim();

    if (!ownerName && i === 0 && !isPlaceholderOwnerName(currentOwnerName)) {
      ownerName = String(currentOwnerName).trim();
    }
    if (!ownerName || isPlaceholderOwnerName(ownerName)) continue;

    const spanMatch = /^(\d{4})(?:-(\d{4}))?$/.exec(spanRaw);
    if (!spanMatch) continue;
    const startYear = Number(spanMatch[1]);
    const endYear = spanMatch[2] ? Number(spanMatch[2]) : startYear;
    if (!Number.isFinite(startYear) || !Number.isFinite(endYear) || endYear < startYear) continue;
    const spanLabel = startYear === endYear ? String(startYear) : `${startYear}-${endYear}`;

    let acqDate = "";
    let acqLabel = "";
    let isPreMainframe = false;
    let isPre2005 = false;
    if (acqRaw === "<=1988") {
      acqDate = "<=1988";
      acqLabel = "Acq. <=1988 (Pre-Mainframe)";
      isPreMainframe = true;
      isPre2005 = true;
    } else if (/^\d{8}$/.test(acqRaw) && acqRaw !== "00000000") {
      const yyyy = acqRaw.slice(0, 4);
      const mm = acqRaw.slice(4, 6);
      const dd = acqRaw.slice(6, 8);
      acqDate = mm === "00" || dd === "00" ? yyyy : `${yyyy}-${mm}-${dd}`;
      acqLabel = `Acq. ${acqDate}`;
      isPre2005 = Number(yyyy) < 2005;
    } else if (/^\d{4}-\d{2}-\d{2}$/.test(acqRaw)) {
      acqDate = acqRaw;
      acqLabel = `Acq. ${acqDate}`;
      isPre2005 = Number(acqRaw.slice(0, 4)) < 2005;
    } else if (/^\d{4}$/.test(acqRaw)) {
      acqDate = acqRaw;
      acqLabel = `Acq. ${acqDate}`;
      isPre2005 = Number(acqRaw) < 2005;
    }

    const deedDateHint =
      acqDate && acqDate !== "<=1988" ? acqDate : String(startYear);
    const deed = deedRaw ? formatClerkInstrumentToken(deedRaw, deedDateHint) : null;
    const partyTokens = formatClerkPartyTokens(ownerName);

    tenures.push({
      startYear,
      endYear,
      spanLabel,
      acqDate,
      acqLabel,
      isPreMainframe,
      isPre2005,
      ownerName,
      deed,
      partyTokens,
    });
  }
  return tenures;
}

function _escapeClerkHtml(val) {
  return String(val ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function _renderClerkDeedPillHtml(item) {
  if (!item) return "";
  const dateTag = item.date
    ? `<span class="rk-deed-date-tag">${_escapeClerkHtml(item.date)}</span>`
    : "";
  const partyPair =
    item.grantor && item.grantee
      ? `${item.grantor} -> ${item.grantee}`
      : item.grantee
      ? `Grantee: ${item.grantee}`
      : item.grantor
      ? `Grantor: ${item.grantor}`
      : "";
  const partyTag = partyPair
    ? `<span class="rk-deed-parties">${_escapeClerkHtml(partyPair)}</span>`
    : "";
  if (item.kind === "vol_page" && item.pageValue) {
    const escVol = _escapeClerkHtml(item.copyValue);
    const escPg = _escapeClerkHtml(item.pageValue);
    const vpHint = `Pre-1961 Bound Deed Book transfer (${
      item.date || "Archival"
    })${partyPair ? ` [${partyPair}]` : ""}: click to copy Volume '${item.copyValue}' (Page '${item.pageValue}')`;
    return `<button
        type="button"
        class="rk-copy-pill deed-fileno"
        data-rk-copy="${escVol}"
        title="${_escapeClerkHtml(vpHint)}"
      >
        ${dateTag}
        <span class="rk-copy-k">Deed Vol/Pg:</span>
        <span class="rk-copy-v mono">${_escapeClerkHtml(item.displayValue)}</span>${partyTag}
        <span class="rk-copy-icon" aria-hidden="true">&#10697;</span>
      </button>`;
  }
  const escCopy = _escapeClerkHtml(item.copyValue);
  const escLbl = _escapeClerkHtml(item.clerkBoxLabel);
  const escVal = _escapeClerkHtml(item.displayValue);
  const basePortalHint =
    item.portal === "probate"
      ? "Paste into Harris County Probate Court Search"
      : `Paste '${item.copyValue}' alone into the '${item.clerkBoxLabel}' box on Harris County Clerk RP and press Search`;
  const portalHint = partyPair ? `${partyPair} | ${basePortalHint}` : basePortalHint;
  return `<button
      type="button"
      class="rk-copy-pill deed-fileno"
      data-rk-copy="${escCopy}"
      title="${_escapeClerkHtml(portalHint)}"
    >
      ${dateTag}
      <span class="rk-copy-k">${escLbl}:</span>
      <span class="rk-copy-v mono">${escVal}</span>${partyTag}
      <span class="rk-copy-icon" aria-hidden="true">&#10697;</span>
    </button>`;
}

function _renderMechanicsLiensSubblockHtml(mlItems) {
  if (!Array.isArray(mlItems) || mlItems.length === 0) return "";
  const pills = mlItems
    .map((ml) => {
      const pillHtml = _renderClerkDeedPillHtml(ml.instrument);
      const amtTag = ml.amount ? ` ($${_escapeClerkHtml(ml.amount)})` : "";
      const ownTag = ml.ownerName ? ` for ${_escapeClerkHtml(ml.ownerName)}` : "";
      return `<div class="rk-ml-entry">
        <span class="rk-ml-contractor">${_escapeClerkHtml(ml.contractor)}${amtTag}${ownTag}</span>
        ${pillHtml}
      </div>`;
    })
    .join("");
  return `<div class="rk-unpaired-deeds-subhead">Recorded Mechanic's Liens &amp; Builder Contracts (M/L)</div>
    <div class="rk-ml-list">${pills}</div>`;
}

function _renderDeedOfTrustLendersSubblockHtml(dtItems) {
  if (!Array.isArray(dtItems) || dtItems.length === 0) return "";
  const pills = dtItems
    .map((dt) => {
      const pillHtml = _renderClerkDeedPillHtml(dt.instrument);
      return `<div class="rk-ml-entry rk-dt-entry">
        <span class="rk-ml-contractor rk-dt-lender">${_escapeClerkHtml(dt.lender)}</span>
        ${pillHtml}
      </div>`;
    })
    .join("");
  return `<div class="rk-unpaired-deeds-subhead">Recorded Deeds of Trust &amp; Institutional Financing (D/T)</div>
    <div class="rk-ml-list">${pills}</div>`;
}

/**
 * Summarize HCAD CAMA deeds.txt coverage for Step 3 Recorded Deed Transfers so properties
 * whose last ownership change predates HCAD's mid-2004 database cutover display a transparent
 * explanation rather than an unexplained blank gap.
 */
export function describeDeedChainCoverage(chainItems = []) {
  const list = Array.isArray(chainItems) ? chainItems : [];
  if (list.length > 0) {
    const hasPre2004 = list.some(
      (it) => /^\d{4}/.test(String(it?.date || "")) && Number(String(it.date).slice(0, 4)) < 2004
    );
    return {
      hasRecordedFilings: true,
      count: list.length,
      title: "Recorded Deed Transfers (1-Click Clerk File Number)",
      countLabel: `${list.length} Recorded ${list.length === 1 ? "Filing" : "Filings"}`,
      scopeNote: hasPre2004
        ? "HCAD's public deed index primarily covers post-mid-2004 ownership sales (with partial pre-2004 backfill) and omits mortgages (D/T), liens (M/L), and releases. For complete 1961-present history, search using the 1-Click Copy Tokens right below."
        : "HCAD's public deed index covers post-mid-2004 ownership sales only. For pre-2004 deeds, mortgages (D/T), liens (M/L), and releases, search using the 1-Click Copy Tokens right below.",
      emptyExplanation: "",
    };
  }
  return {
    hasRecordedFilings: false,
    count: 0,
    title: "Recorded Deed Transfers (HCAD Bulk Index)",
    countLabel: "No Post-2004 HCAD Sales",
    scopeNote: "",
    emptyExplanation:
      "HCAD's public deed table only indexes title-transferring sales recorded after its mid-2004 database cutover (and omits mortgages, liens, and releases). Use the 1-Click Copy Tokens right below (paste Subdivision or Description + Lot + Block, or Grantee) on Harris County Clerk Deeds (RP) to view all 1961-present recorded instruments.",
  };
}

/**
 * Pure, DOM-free view-model and HTML builder for the Step 3 Recorded Deed & Ownership Chain box.
 * Returns { ignore, hidden, isEmptyCama, count, html }.
 */
export function buildResearchKitDeedChainView(
  hcadNum,
  activeBoxHcadNum,
  permitShardRec,
  currentOwnerName = ""
) {
  const cleanHcad = String(hcadNum || "").trim();
  if (String(activeBoxHcadNum || "").trim() !== cleanHcad) {
    return { ignore: true, hidden: false, isEmptyCama: false, count: 0, html: "" };
  }
  if (!/^(?!0+$)\d{10,13}$/.test(cleanHcad)) {
    return { ignore: false, hidden: true, isEmptyCama: false, count: 0, html: "" };
  }

  const effOwner = String(currentOwnerName || permitShardRec?.owner || "").trim();
  const isConfidential = Boolean(
    permitShardRec?.conf ||
      isConfidentialOwnerName(currentOwnerName) ||
      isConfidentialOwnerName(permitShardRec?.owner)
  );
  if (isConfidential) {
    const html = `
      <div class="rk-deed-chain-head">
        <span class="rk-deed-chain-title">&#128220; HCAD Roll Ownership &amp; Deed Chain</span>
        <span class="rk-deed-chain-count is-muted">Protected (TX Tax Code Sec. 25.025)</span>
      </div>
      <div class="rk-clerk-hint">
        Ownership names and County Clerk instrument links are withheld for statutory confidential homesteads under Texas Tax Code Section 25.025.
      </div>
    `;
    return { ignore: false, hidden: false, isEmptyCama: true, count: 0, html };
  }
  const tenures = decodeCompactOwnerHistory(permitShardRec?.oh || "", effOwner);
  const chainItems = decodeCompactDeedChain(permitShardRec?.d || "");
  const mlItems = decodeCompactMechanicsLiens(permitShardRec?.ml || "");
  const dtItems = decodeCompactDeedOfTrustLenders(permitShardRec?.dt_lender || "");
  const mlHtml = _renderMechanicsLiensSubblockHtml(mlItems);
  const dtHtml = _renderDeedOfTrustLendersSubblockHtml(dtItems);

  // When 22-year HCAD certified-roll ownership tenures are available for a non-confidential parcel,
  // render the integrated Ownership & Deed Chain view while retaining any unpaired Clerk instruments (`REG-1`).
  if (tenures.length > 0) {
    const pairedKeys = new Set(
      tenures.map((t) => t.deed?.matchKey).filter(Boolean)
    );
    const unpairedDeeds = chainItems.filter(
      (it) => !it.matchKey || !pairedKeys.has(it.matchKey)
    );
    const maxEndYear = tenures.reduce(
      (acc, t) => Math.max(acc, t.endYear || tenures[0].endYear),
      tenures[0].endYear
    );
    const minStartYear = tenures.reduce(
      (acc, t) => Math.min(acc, t.startYear || tenures[0].startYear),
      tenures[0].startYear
    );

    const tenureRowsHtml = tenures
      .map((t, idx) => {
        const acqTagHtml = t.acqLabel
          ? `<span class="rk-owner-tenure-acq mono">${_escapeClerkHtml(t.acqLabel)}</span>`
          : "";
        const ownerPillsHtml = t.partyTokens
          .slice(0, 2)
          .map((pt, pIdx) => {
            const escCopy = _escapeClerkHtml(pt.copy);
            const roleLbl =
              idx === 0
                ? pIdx === 0
                  ? "Copy Owner"
                  : `Copy Owner (${pIdx + 1})`
                : pIdx === 0
                ? "Copy Prior Owner"
                : `Copy Prior (${pIdx + 1})`;
            return `<button
              type="button"
              class="rk-copy-pill owner-party"
              data-rk-copy="${escCopy}"
              title="Click to copy '${escCopy}' (Last Name First - No Punctuation) for the Grantee or Grantor box on Harris County Clerk RP"
            >
              <span class="rk-copy-k">${_escapeClerkHtml(roleLbl)}:</span>
              <span class="rk-copy-v mono">${_escapeClerkHtml(pt.display)}</span>
              <span class="rk-copy-icon" aria-hidden="true">&#10697;</span>
            </button>`;
          })
          .join("");
        const pairedDeedHtml = t.deed ? _renderClerkDeedPillHtml(t.deed) : "";
        const pillsBlock =
          ownerPillsHtml || pairedDeedHtml
            ? `<div class="rk-owner-tenure-pills">${pairedDeedHtml}${ownerPillsHtml}</div>`
            : "";
        return `<div class="rk-owner-tenure-row">
            <div class="rk-owner-tenure-meta">
              <span class="rk-owner-tenure-span mono">${_escapeClerkHtml(t.spanLabel)}</span>
              ${acqTagHtml}
              <span class="rk-owner-tenure-name">${_escapeClerkHtml(t.ownerName)}</span>
            </div>
            ${pillsBlock}
          </div>`;
      })
      .join("");

    const unpairedHtml =
      unpairedDeeds.length > 0
        ? `<div class="rk-unpaired-deeds-subhead">Additional Recorded Clerk Transfers</div>
           <div class="rk-copy-pills-wrap">
             ${unpairedDeeds.map((it) => _renderClerkDeedPillHtml(it)).join("")}
           </div>`
        : "";

    let scopeNote =
      "Combines 22 annual HCAD certified appraisal rolls (2005-present) with HCAD's post-mid-2004 deed index (pre-2005 HCAD CAMA tables were not retained across HCAD server conversions). Copy any File Number or Prior Owner token above into County Clerk Deeds (RP, 1960-present) to pull deeds, mortgages (D/T), and mechanic's liens (M/L).";
    if (tenures.length === 1 && chainItems.length === 0) {
      const t0 = tenures[0];
      if (t0.isPreMainframe) {
        scopeNote =
          "Continuous HCAD Roll Tenure Since <=1988 (HCAD Mainframe Baseline): On January 2, 1988 (01/02/1988), HCAD digitized the 1983 microfilm tax rolls transferred from the Harris County Tax Assessor-Collector's Office, and subsequent server migrations dropped pre-2005 electronic CAMA tables. Copy the Owner token above or Legal Description below into County Clerk Deeds (RP, 1960-present), or contact Harris County Archives (archives@hctx.net, 1983-1985 rolls) to trace pre-1988 instruments.";
      } else if (t0.isPre2005 || t0.startYear <= 2005) {
        scopeNote = `Continuous HCAD Roll Tenure (${t0.spanLabel}${
          t0.acqDate ? ` · Acq. ${t0.acqDate}` : ""
        }): Zero post-2005 HCAD ownership changes recorded. Copy the Owner token above or Legal Description below into County Clerk Deeds (RP) for pre-2004 deeds, mortgages, and liens.`;
      } else {
        scopeNote = `Parcel Account Created / Replatted in ${t0.startYear} (${t0.spanLabel}): Search the Historic Parent Plat or Subdivision + Block Tokens below on County Clerk Deeds (RP) to trace pre-${t0.startYear} parent-tract deeds.`;
      }
    }

    const countVal = tenures.length + unpairedDeeds.length + mlItems.length + dtItems.length;
    const countBadge =
      tenures.length === 1
        ? `1 Continuous Tenure (${tenures[0].spanLabel})`
        : `${tenures.length} HCAD Owners (${minStartYear}-${maxEndYear})`;

    const html = `
      <div class="rk-deed-chain-head">
        <span class="rk-deed-chain-title">&#128220; HCAD Roll Ownership &amp; Deed Chain (${minStartYear}-${maxEndYear})</span>
        <span class="rk-deed-chain-count">${_escapeClerkHtml(countBadge)}</span>
      </div>
      <div class="rk-clerk-hint">
        <strong>1-Click Clerk Search:</strong> copy a <strong>File Number</strong> for an instant deed match, or copy any <strong>Prior Owner</strong> token (formatted <em>Last Name First, No Punctuation</em>) into the <strong>Grantee</strong> or <strong>Grantor</strong> box on County Clerk Deeds (RP).
      </div>
      <div class="rk-owner-tenure-list">
        ${tenureRowsHtml}
      </div>
      ${unpairedHtml}${mlHtml ? `\n      ${mlHtml}` : ""}${dtHtml ? `\n      ${dtHtml}` : ""}
      <div class="rk-deed-scope-note">
        ${_escapeClerkHtml(scopeNote)}
      </div>
    `;
    return { ignore: false, hidden: false, isEmptyCama: false, count: countVal, html };
  }

  // Fallback when `oh` is absent or confidential: preserve legacy HCAD deed index rendering (`REG-1`, `REG-4`).
  const coverage = describeDeedChainCoverage(chainItems);
  if (!coverage.hasRecordedFilings && mlItems.length === 0 && dtItems.length === 0) {
    const html = `
        <div class="rk-deed-chain-head">
          <span class="rk-deed-chain-title">&#128220; ${_escapeClerkHtml(coverage.title)}</span>
          <span class="rk-deed-chain-count is-muted">${_escapeClerkHtml(coverage.countLabel)}</span>
        </div>
        <div class="rk-clerk-hint">
          ${_escapeClerkHtml(coverage.emptyExplanation)}
        </div>
      `;
    return { ignore: false, hidden: false, isEmptyCama: true, count: 0, html };
  }

  const pillsHtml = chainItems.map((item) => _renderClerkDeedPillHtml(item)).join("");
  const extraFilingsCount = mlItems.length + dtItems.length;
  const effCount = coverage.count + extraFilingsCount;
  const effCountLabel = coverage.hasRecordedFilings
    ? coverage.countLabel
    : mlItems.length > 0 && dtItems.length === 0
    ? `${mlItems.length} Recorded ${mlItems.length === 1 ? "Lien" : "Liens"}`
    : `${extraFilingsCount} Recorded ${extraFilingsCount === 1 ? "Filing" : "Filings"}`;

  const html = `
      <div class="rk-deed-chain-head">
        <span class="rk-deed-chain-title">&#128220; ${_escapeClerkHtml(coverage.title)}</span>
        <span class="rk-deed-chain-count">${_escapeClerkHtml(effCountLabel)}</span>
      </div>
      <div class="rk-clerk-hint">
        <strong>Fastest lookup:</strong> copy any single <strong>File Number</strong> below, paste it alone into the top-left <strong>File Number</strong> box on County Clerk Deeds (RP), and click Search. Open the oldest deed to read the prior Grantor (seller) name.
      </div>
      ${pillsHtml ? `<div class="rk-copy-pills-wrap">\n        ${pillsHtml}\n      </div>` : ""}${
    mlHtml ? `\n      ${mlHtml}` : ""
  }${dtHtml ? `\n      ${dtHtml}` : ""}
      <div class="rk-deed-scope-note">
        ${_escapeClerkHtml(coverage.scopeNote || coverage.emptyExplanation)}
      </div>
    `;
  return { ignore: false, hidden: false, isEmptyCama: false, count: effCount, html };
}
