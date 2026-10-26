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
  "LLC", "L\\s+L\\s+C", "INC", "INCORPORATED", "CORP", "CORPORATION",
  "COMPANY", "CO", "LP", "LTD", "LIMITED", "LLP", "L\\s+L\\s+P", "PLC",
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
  "LOFTS", "PLAZA", "CENTER", "CENTRE", "RR", "RY", "RAILROAD", "RAILWAY",
];

const ENTITY_KEYWORD_RE = new RegExp(
  `\\b(?:${ENTITY_KEYWORD_PATTERNS.join("|")})\\b`,
  "i"
);

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
  if (!orig) return [];
  // Strip HCAD care-of ("% AGENT NAME" or "C/O ...") mailing suffix before entity/person classification
  const upperOrig = orig
    .toUpperCase()
    .replace(/\s*(?:(?<!\d)%|\bC\/O\b).*$/, "")
    .trim();
  if (
    !upperOrig ||
    upperOrig === "PUBLIC / UNLISTED" ||
    upperOrig === "CURRENT OWNER" ||
    upperOrig === "UNLISTED" ||
    upperOrig === "UNKNOWN" ||
    upperOrig === "PENDING"
  ) {
    return [];
  }

  // Collapse dotted abbreviations like "L.L.C." -> "LLC" and "L.P." -> "LP" before punctuation removal
  const collapsedAbbrev = upperOrig.replace(
    /\b([A-Z])\.([A-Z])\.(?:([A-Z])\.)?/g,
    (_, a, b, c) => `${a}${b}${c || ""}`
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
 * Decode a compact deed chain string ("20190329:R2019-149789|20120820:20120430232|...")
 * stored in permits_<prefix3>.json["<hcad_num>"].d into structured Clerk lookup items.
 */
export function decodeCompactDeedChain(compactStr = "") {
  const raw = String(compactStr || "").trim();
  if (!raw) return [];

  const items = [];
  for (const entry of raw.split("|")) {
    if (!entry) continue;
    const colonIdx = entry.indexOf(":");
    if (colonIdx <= 0) continue;
    const dt8 = entry.slice(0, colonIdx).trim();
    const token = entry.slice(colonIdx + 1).trim();
    if (!token) continue;

    let dateDisplay = "";
    if (/^\d{8}$/.test(dt8) && dt8 !== "00000000") {
      const yyyy = dt8.slice(0, 4);
      const mm = dt8.slice(4, 6);
      const dd = dt8.slice(6, 8);
      dateDisplay = mm === "00" || dd === "00" ? yyyy : `${yyyy}-${mm}-${dd}`;
    }

    if (token.startsWith("FC:")) {
      const filmCode = token.slice(3);
      items.push({
        date: dateDisplay,
        kind: "film_code",
        clerkBoxLabel: "Film Code",
        copyValue: filmCode,
        displayValue: filmCode,
        portal: "rp",
      });
    } else if (token.startsWith("PR:")) {
      const caseNo = token.slice(3);
      items.push({
        date: dateDisplay,
        kind: "probate",
        clerkBoxLabel: "Probate Case #",
        copyValue: caseNo,
        displayValue: caseNo,
        portal: "probate",
      });
    } else if (token.startsWith("VP:")) {
      const [vol = "", pg = ""] = token.slice(3).split("/");
      items.push({
        date: dateDisplay,
        kind: "vol_page",
        clerkBoxLabel: "Vol / Page",
        copyValue: vol,
        pageValue: pg,
        displayValue: `Vol ${vol}, Pg ${pg}`,
        portal: "archives",
      });
    } else {
      const fileNumber = /^R20\d{2}-\d+$/.test(token)
        ? `RP-${token.slice(1)}`
        : token;
      items.push({
        date: dateDisplay,
        kind: "file_number",
        clerkBoxLabel: "File Number",
        copyValue: fileNumber,
        displayValue: fileNumber,
        portal: "rp",
      });
    }
  }
  return items;
}
