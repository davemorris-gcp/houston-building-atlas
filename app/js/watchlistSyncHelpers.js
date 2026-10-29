/**
 * Pure, zero-DOM helper functions for the 15-Point Property Watchlist, Portal Copy Pills,
 * Dangerous Buildings (DONDB*.csv) Countywide Cross-Checker, Link Sanitization, and
 * Ownership-Partitioned 3-Way Watchlist State Reconciliation.
 *
 * Composes directly with:
 *   - ./clerkSearchHelpers.js for Harris County Clerk Legal Description & Grantee/Grantor tokens
 *   - ./platMonitorHelpers.js for HCAD account normalization, Threat Categories, and human-dossier guards
 */

import {
  parseClerkLegalTokens,
  formatClerkPartyTokens,
  cleanClerkSubdivisionDesc,
} from "./clerkSearchHelpers.js?v=20261029a";
import {
  THREAT_CATEGORIES,
  parseHcadAccountList,
  hasHumanDossierContributions,
} from "./platMonitorHelpers.js?v=20261029a";

export const TRACKER_STATUSES = [
  "Active / High Priority",
  "Dormant / Medium Priority",
  "For Sale",
  "Monitoring",
  "Saved",
  "Lost",
  "Archived (Tier 3)",
];

export const REMOVED_TIER3_TOMBSTONE = "Removed (Tier 3)";

export const SEARCH_TERM_CATEGORIES = [
  { key: "entities", label: "LLCs / Entities", placeholder: "e.g. Smith St C Store LLC" },
  { key: "dbas", label: "DBAs / Brands", placeholder: "e.g. Brake Time" },
  { key: "officers", label: "Officers / Agents", placeholder: "e.g. Omair Bashir" },
  { key: "legalKeywords", label: "Block / Subdiv Keywords", placeholder: "e.g. Block 12, Hadley" },
  { key: "altAddresses", label: "Perimeter / Alt Addresses", placeholder: "e.g. 530 Pierce St." },
  { key: "projectNumbers", label: "Permit / ILMS Project #s", placeholder: "e.g. 26052089" },
];

export const DEFAULT_KIRBY_SEARCH_TERMS = {
  entities: [
    "OB Texas Properties LLC",
    "Smith St C Store LLC",
    "BTCM National Retail Operations LLC",
    "Hi Tech Auto Realty LLC",
  ],
  dbas: ["Brake Time", "Braketime"],
  officers: ["Omair Bashir"],
  legalKeywords: ["Block 417", "SSBB", "Block 12", "Hadley"],
  altAddresses: ["530 Pierce St.", "2000 Brazos St.", "500 Gray St."],
  projectNumbers: ["26052089", "26090093", "TABS2026022839"],
};

const STREET_TYPE_SUFFIXES = new Set([
  "ST",
  "STREET",
  "AVE",
  "AVENUE",
  "RD",
  "ROAD",
  "DR",
  "DRIVE",
  "BLVD",
  "BOULEVARD",
  "LN",
  "LANE",
  "CT",
  "COURT",
  "PL",
  "PLACE",
  "WAY",
  "PKWY",
  "PARKWAY",
  "CIR",
  "CIRCLE",
  "TRL",
  "TRAIL",
  "TER",
  "TERRACE",
  "FWY",
  "FREEWAY",
  "EXPY",
  "EXPRESSWAY",
  "SQ",
  "SQUARE",
  "PLZ",
  "PLAZA",
  "ALY",
  "ALLEY",
  "PATH",
  "ROW",
  "WALK",
  "CV",
  "COVE",
  "PT",
  "POINT",
]);

const DIRECTIONAL_TOKENS = new Set([
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

const SUBDIVISION_LEADING_MODIFIERS = new Set([
  "NORTH",
  "SOUTH",
  "EAST",
  "WEST",
  "N",
  "S",
  "E",
  "W",
  "NE",
  "NW",
  "SE",
  "SW",
  "UPPER",
  "LOWER",
  "GREATER",
  "THE",
  "TRS",
  "TR",
  "TRACT",
  "TRACTS",
  "LT",
  "LTS",
  "LOT",
  "LOTS",
  "RES",
  "RESERVE",
  "BLK",
  "BLKS",
  "BLOCK",
  "BLOCKS",
]);

/**
 * Simplify a Houston situs street address for external municipal/state portal searches.
 *
 * Why positional tokenization: Municipal portals inconsistently abbreviate or omit street suffixes
 * ("3827 Broadway St" vs "3827 Broadway"), while HCAD appends bare condo unit numbers directly after
 * street suffixes ("909 TEXAS AVE 308", "1 MAIN ST 562"). Unanchored regex replacement would corrupt
 * Saint-prefixed streets ("2100 St Emanuel St"), East End lettered avenues ("1200 Ave A"), or leave
 * trailing bare unit numbers attached.
 */
export function simplifySearchAddress(rawAddr = "") {
  let s = String(rawAddr || "").trim();
  if (!s) return "";

  // Take first street segment before semicolon or city/state/zip tail
  s = s.split(";")[0].trim();
  s = s.replace(/,\s*Houston\b.*$/i, "");
  s = s.replace(/,\s*TX\b(?:\s+\d{5}(?:-\d{4})?)?$/i, "");
  s = s.replace(/\s+\bHouston\s*,?\s*TX\b(?:\s+\d{5}(?:-\d{4})?)?$/i, "");

  // Strip explicit #unit designators and named unit/suite/floor tails
  s = s.replace(/\s+#\s*[A-Za-z0-9-]+.*$/, "");
  s = s.replace(
    /\s*,?\s*\b(?:STE|SUITE|APT|APARTMENT|UNIT|BLDG|BUILDING|FL|FLOOR|RM|ROOM|DEPT|SPC|SPACE)\b\.?(?:\s+[A-Za-z0-9#-]+.*)?$/i,
    ""
  );

  const rawTokens = s
    .split(/\s+/)
    .map((tok) => tok.replace(/^[.,]+|[.,]+$/g, ""))
    .filter(Boolean);
  if (rawTokens.length === 0) return "";

  const tokens = rawTokens.slice();
  const hasLeadingNumber = /^\d+[A-Za-z]?(?:-\d+[A-Za-z]?)?$/.test(tokens[0]);
  let streetStartIdx = hasLeadingNumber ? 1 : 0;
  if (hasLeadingNumber && tokens[1] === "1/2") {
    streetStartIdx = 2;
  }

  // Protect East End lettered avenues ("1200 Ave A", "6500 Avenue J", "300 N Ave C")
  const streetSlice = tokens.slice(streetStartIdx);
  if (
    streetSlice.length >= 2 &&
    /^[A-Z]$/i.test(streetSlice[streetSlice.length - 1]) &&
    /^(?:AVE|AVENUE)$/i.test(streetSlice[streetSlice.length - 2])
  ) {
    const preAve = streetSlice.slice(0, -2);
    if (preAve.length === 0 || preAve.every((t) => DIRECTIONAL_TOKENS.has(t.toUpperCase()))) {
      return tokens.join(" ");
    }
  }

  // Strip bare HCAD trailing unit numbers after a recognized street-type suffix ("909 TEXAS AVE 308")
  if (tokens.length - streetStartIdx >= 3) {
    const penultUpper = tokens[tokens.length - 2].toUpperCase();
    const lastTok = tokens[tokens.length - 1];
    const coreBeforePenult = tokens
      .slice(streetStartIdx, tokens.length - 2)
      .filter((t) => !DIRECTIONAL_TOKENS.has(t.toUpperCase()));
    if (
      STREET_TYPE_SUFFIXES.has(penultUpper) &&
      coreBeforePenult.length >= 1 &&
      /^(?:\d+[A-Za-z]?|[A-Za-z]\d+)$/.test(lastTok)
    ) {
      tokens.pop();
    }
  }

  // Strip trailing street-type suffix only when preceded by >= 1 non-directional street-core token
  if (tokens.length - streetStartIdx >= 2) {
    const lastUpper = tokens[tokens.length - 1].toUpperCase();
    if (STREET_TYPE_SUFFIXES.has(lastUpper)) {
      const coreBeforeSuffix = tokens
        .slice(streetStartIdx, tokens.length - 1)
        .filter((t) => !DIRECTIONAL_TOKENS.has(t.toUpperCase()));
      if (coreBeforeSuffix.length >= 1) {
        tokens.pop();
      }
    }
  }

  return tokens.join(" ");
}

/**
 * Pre-segment single-line HCAD legal summary strings (e.g. "LT 12 BLK 34 HADLEY ADDITION",
 * "TRS 1-16 BLK 417 SSBB", "RES A BLK 1 NORTH NORHILL") into [lotBlockLine, additionLine]
 * before delegating tokenization to parseClerkLegalTokens() in clerkSearchHelpers.js.
 */
function preSegmentLegalInput(legalDescription = "") {
  if (Array.isArray(legalDescription)) return legalDescription;
  const raw = String(legalDescription || "").trim();
  if (!raw) return [];
  if (raw.includes("|") || raw.includes("·") || raw.includes("\n")) {
    return raw
      .split(/[|·\n]+/)
      .map((s) => s.trim())
      .filter(Boolean);
  }

  // Single-line legal string containing BLK <id> followed by an alphabetic subdivision/addition tail
  const postBlkMatch = raw.match(
    /^(.*?\b(?:BLKS?|BLOCKS?)\s+[0-9A-Z-]+(?:\s+(?:SEC|SECT|SECTION)\s+[0-9A-Z-]+)?)\s+([A-Z][A-Z0-9\s.'&-]{1,})$/i
  );
  if (postBlkMatch) {
    const candidateTail = postBlkMatch[2].trim();
    if (!/^(?:LT|LTS|LOT|LOTS|TR|TRS|TRACT|TRACTS|RES|RESERVE|ABST|ABSTRACT)\b/i.test(candidateTail)) {
      return [postBlkMatch[1].trim(), candidateTail];
    }
  }

  // Single-line Lot/Tract/Reserve without Block or Abstract (e.g. "RES B HYDE PARK ADDITION")
  if (!/\b(?:ABST|ABSTRACT|BLKS?|BLOCKS?)\b/i.test(raw)) {
    const postLotMatch = raw.match(
      /^((?:LTS?|LOTS?|TRS?|TRACTS?|RES|RESERVE)\s+(?:\d+[A-Z]?(?:-\d+[A-Z]?)?|[A-Z]\b)(?:\s*(?:&|,|THRU|TO)\s*(?:\d+[A-Z]?|[A-Z]\b))*)\s+([A-Z]{2,}[A-Z0-9\s.'&-]*)$/i
    );
    if (postLotMatch) {
      return [postLotMatch[1].trim(), postLotMatch[2].trim()];
    }
  }

  return [raw];
}

/**
 * Extract Harris County Clerk Real Property (RP.aspx) Block and distinctive Subdivision search
 * terms by composing directly with app/js/clerkSearchHelpers.js.
 */
export function extractClerkLegalCopyTerms(legalDescription = "", subdivisionFallback = "") {
  const legalLines = preSegmentLegalInput(legalDescription);
  const clerkTokens = parseClerkLegalTokens({
    legalLines,
    subdivision: subdivisionFallback,
  });

  const isPureAbstractWithoutPlat =
    Boolean(clerkTokens.abstract) && !clerkTokens.addition && !subdivisionFallback;

  const subdivisionFull = isPureAbstractWithoutPlat
    ? ""
    : clerkTokens.subdivisionDesc || cleanClerkSubdivisionDesc(subdivisionFallback || "");

  let subdivisionKeyword = "";
  if (subdivisionFull) {
    const words = subdivisionFull
      .split(/\s+/)
      .map((w) => w.replace(/[^A-Z0-9]/gi, "").toUpperCase())
      .filter(Boolean);
    const distinctive = words.find((w) => w.length >= 2 && !SUBDIVISION_LEADING_MODIFIERS.has(w));
    subdivisionKeyword = distinctive || words[0] || "";
  }

  return {
    block: clerkTokens.block || "",
    subdivisionFull,
    subdivisionKeyword,
    lot: clerkTokens.lot || "",
    tract: clerkTokens.tract || "",
    reserve: clerkTokens.reserve || "",
    section: clerkTokens.section || "",
    abstract: clerkTokens.abstract || "",
    abstractSurvey: clerkTokens.abstractSurvey || "",
    clerkTokens,
  };
}

export function escapeHtmlText(val = "") {
  return String(val ?? "").replace(
    /[&<>"']/g,
    (ch) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      }[ch])
  );
}

export function escapeXmlText(val = "") {
  return String(val ?? "")
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, "")
    .replace(
      /[&<>"']/g,
      (ch) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&apos;",
        }[ch])
    );
}

/**
 * Validate an external result or Google Drive URL so only well-formed http: and https: URLs
 * are stored, synced, or rendered into HTML / WordprocessingML (.docx) sinks.
 */
export function sanitizeExternalUrl(rawUrl = "") {
  const trimmed = String(rawUrl ?? "").trim();
  if (!trimmed) return "";
  if (/[\u0000-\u001F\u007F]/.test(trimmed)) return "";
  if (!/^https?:\/\//i.test(trimmed)) return "";
  try {
    const parsed = new URL(trimmed);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return "";
    if (!parsed.hostname || !parsed.hostname.includes(".")) {
      if (parsed.hostname !== "localhost") return "";
    }
    return parsed.href;
  } catch (_err) {
    return "";
  }
}

export function sanitizeExternalLink(entry) {
  if (!entry) return null;
  const rawUrl = typeof entry === "string" ? entry : entry.url;
  const safeUrl = sanitizeExternalUrl(rawUrl);
  if (!safeUrl) return null;

  let label =
    typeof entry === "object" && entry.label != null
      ? String(entry.label).replace(/[\u0000-\u001F]+/g, " ").trim()
      : "";
  if (!label) {
    try {
      const u = new URL(safeUrl);
      label = u.hostname.includes("drive.google.com") || u.hostname.includes("docs.google.com")
        ? "Google Drive Document"
        : u.hostname.replace(/^www\./i, "");
    } catch (_e) {
      label = "Linked Result";
    }
  }
  const out = { label, url: safeUrl };
  if (typeof entry === "object" && entry.addedAt) out.addedAt = String(entry.addedAt);
  if (typeof entry === "object" && entry.addedBy) out.addedBy = String(entry.addedBy);
  return out;
}

export function sanitizeExternalLinksList(list) {
  if (!Array.isArray(list)) return [];
  const seen = new Set();
  const out = [];
  for (const item of list) {
    const clean = sanitizeExternalLink(item);
    if (clean && !seen.has(clean.url)) {
      seen.add(clean.url);
      out.push(clean);
    }
  }
  return out;
}

export function buildDefaultChecklist(addr = "") {
  const qAddr = encodeURIComponent((String(addr || "").trim() || "Houston") + ", Houston, TX");
  return [
    {
      id: "chk-01",
      stage: "Every Check",
      activity: "Public Notices",
      whatToLookFor:
        "Search the simplified street address, owner, and buyer names for published legal notices, such as TABC application notices.",
      typicalTiming: "Any stage.",
      resourceLabel: "Texas Public Notices",
      resourceUrl: "https://www.texaspublicnotices.com/",
      additionalResourceLabel: "COH Planning Notices",
      additionalResourceUrl: "https://www.houstontx.gov/planning/notices/",
      status: "To Do",
      lastChecked: "",
      automated: false,
      checks: [],
    },
    {
      id: "chk-02",
      stage: "Every Check",
      activity: "Site Visit",
      whatToLookFor:
        "Drive by and photograph the site. Look for TABC 60-day signs, plat or variance notice signs, fencing, dumpsters, utility disconnections, and survey stakes.",
      typicalTiming: "Any stage. Posted signs can appear before records show up online.",
      resourceLabel: "Google Street View",
      resourceUrl: `https://www.google.com/maps/search/?api=1&query=${qAddr}`,
      additionalResourceLabel: "",
      additionalResourceUrl: "",
      status: "To Do",
      lastChecked: "",
      automated: false,
      checks: [],
    },
    {
      id: "chk-03",
      stage: "Sale & Ownership",
      activity: "Deed Transfers & Liens",
      whatToLookFor:
        "Search by Legal Description (Block + Subdivision keyword) and owner/buyer names for deeds, deeds of trust (naming the lender), and memorandums of contract.",
      typicalTiming: "Deeds usually record within days of closing.",
      resourceLabel: "HC Clerk Real Property",
      resourceUrl: "https://www.cclerk.hctx.net/applications/websearch/RP.aspx",
      additionalResourceLabel: "",
      additionalResourceUrl: "",
      status: "To Do",
      lastChecked: "",
      automated: false,
      checks: [],
    },
    {
      id: "chk-04",
      stage: "Sale & Ownership",
      activity: "Corporate Entity Verification",
      whatToLookFor:
        "Look up each LLC or trade entity across TX Comptroller, Bizapedia, Google Search (if no franchise tax), and SOSDirect to find officers and related entities.",
      typicalTiming: "As soon as a new entity name surfaces.",
      resourceLabel: "Comptroller Entity Search",
      resourceUrl: "https://comptroller.texas.gov/taxes/franchise/account-status/search",
      additionalResourceLabel: "Bizapedia Entity Lookup",
      additionalResourceUrl: "https://www.bizapedia.com/",
      status: "To Do",
      lastChecked: "",
      automated: true,
      checks: [],
    },
    {
      id: "chk-05",
      stage: "Sale & Ownership",
      activity: "HCAD Ownership & Classification",
      whatToLookFor:
        "Search the 13-digit HCAD account number to confirm the owner of record, legal description, and changes to land use, value, or improvements.",
      typicalTiming: "Owner updates lag the deed by weeks to months.",
      resourceLabel: "HCAD Property Search",
      resourceUrl: "https://search.hcad.org/",
      additionalResourceLabel: "",
      additionalResourceUrl: "",
      status: "To Do",
      lastChecked: "",
      automated: true,
      checks: [],
    },
    {
      id: "chk-06",
      stage: "Plans & Use",
      activity: "Liquor Licensing",
      whatToLookFor:
        "Search the simplified address and trade names for pending or issued permits. TABC applications often reveal the operator LLC and DBA.",
      typicalTiming: "After closing, once an operator has a lease or deed.",
      resourceLabel: "TABC Public Inquiry",
      resourceUrl: "https://apps.tabc.texas.gov/publicinquiry/",
      additionalResourceLabel: "",
      additionalResourceUrl: "",
      status: "To Do",
      lastChecked: "",
      automated: true,
      checks: [],
    },
    {
      id: "chk-07",
      stage: "Plans & Use",
      activity: "Hemp Licensing",
      whatToLookFor:
        "Search DSHS consumable hemp licenses by entity LLC name, DBA/trade name, or officer name (portal searches by name, not street address).",
      typicalTiming: "After closing, once an operator has a lease or deed.",
      resourceLabel: "DSHS License Search",
      resourceUrl: "https://vo.ras.dshs.state.tx.us/datamart/login.do",
      additionalResourceLabel: "",
      additionalResourceUrl: "",
      status: "To Do",
      lastChecked: "",
      automated: false,
      checks: [],
    },
    {
      id: "chk-08",
      stage: "Plans & Use",
      activity: "Pending Plans (Open Records & ILMS)",
      whatToLookFor:
        "Look up 8-digit permit project numbers (from Houston Sold Permits) in ILMS, or submit a GovQA open records request for unissued architectural plans.",
      typicalTiming: "Most useful after closing, once an architect or engineer is engaged.",
      resourceLabel: "Public Records (GovQA)",
      resourceUrl: "https://houstontx.govqa.us/WEBAPP/_rs/supporthome.aspx",
      additionalResourceLabel: "ILMS Project Status",
      additionalResourceUrl: "https://www.pdinet.pd.houstontx.gov/cohilms/webs/plan_lookup.asp",
      status: "To Do",
      lastChecked: "",
      automated: false,
      checks: [],
    },
    {
      id: "chk-09",
      stage: "Plans & Use",
      activity: "Replats & Variances",
      whatToLookFor:
        "Check the Biweekly Plat & Variance Review tab and COH biweekly Plat Agenda spreadsheets (PlatTracker portal login is restricted to official roles).",
      typicalTiming: "Can be filed before closing with the seller's consent.",
      resourceLabel: "COH Biweekly Plat Reports",
      resourceUrl: "https://www.houstontx.gov/planning/DevelopRegs/dev_reports.html",
      additionalResourceLabel: "COH Planning Notices",
      additionalResourceUrl: "https://www.houstontx.gov/planning/notices/",
      status: "To Do",
      lastChecked: "",
      automated: true,
      checks: [],
    },
    {
      id: "chk-10",
      stage: "Plans & Use",
      activity: "Accessibility Registration (TDLR TABS)",
      whatToLookFor:
        "Search simplified addresses and DBA/facility names in TABS. Registered projects list the owner, design firm, estimated cost, and scope.",
      typicalTiming: "Design phase, before construction starts.",
      resourceLabel: "TDLR TABS Search",
      resourceUrl: "https://www.tdlr.texas.gov/tabs/search",
      additionalResourceLabel: "",
      additionalResourceUrl: "",
      status: "To Do",
      lastChecked: "",
      automated: true,
      checks: [],
    },
    {
      id: "chk-11",
      stage: "Plans & Use",
      activity: "Easements, Abandonments & TIRZ",
      whatToLookFor:
        "Search City Council public agendas on NovusAgenda for street, alley, or utility easement abandonments and TIRZ actions near the property.",
      typicalTiming: "Design phase, usually after closing.",
      resourceLabel: "City Secretary (NovusAgenda)",
      resourceUrl: "https://houston.novusagenda.com/agendapublic/",
      additionalResourceLabel: "",
      additionalResourceUrl: "",
      status: "To Do",
      lastChecked: "",
      automated: false,
      checks: [],
    },
    {
      id: "chk-12",
      stage: "Plans & Use",
      activity: "Environmental Registrations",
      whatToLookFor:
        "Search the simplified address and entity names for regulated-entity records, such as construction stormwater coverage or storage tanks.",
      typicalTiming: "Shortly before construction.",
      resourceLabel: "TCEQ Central Registry",
      resourceUrl: "https://www.tceq.texas.gov/permitting/central_registry",
      additionalResourceLabel: "",
      additionalResourceUrl: "",
      status: "To Do",
      lastChecked: "",
      automated: false,
      checks: [],
    },
    {
      id: "chk-13",
      stage: "Demolition Signals",
      activity: "Code Complaints & Dangerous Buildings",
      whatToLookFor:
        "Check 311 code complaints and cross-check periodic City Dangerous Buildings (DONDB) CSV lists; notify the City coordinator if historic.",
      typicalTiming: "Any stage, especially while the building is vacant.",
      resourceLabel: "311 Service Request Viewer",
      resourceUrl: "https://experience.arcgis.com/experience/cf006ed734814cee9f5b9bcf7abc839f",
      additionalResourceLabel: "Community Code Enforcement",
      additionalResourceUrl: "https://www.houstonpermittingcenter.org/community-code-enforcement",
      status: "To Do",
      lastChecked: "",
      automated: true,
      checks: [],
    },
    {
      id: "chk-14",
      stage: "Demolition Signals",
      activity: "Demolition & Building Permits",
      whatToLookFor:
        "Search Houston Sold Permits by simplified address for demolition, sewer disconnect, and commercial building permits (and note 8-digit project numbers).",
      typicalTiming: "Final weeks before work starts.",
      resourceLabel: "Houston Sold Permits",
      resourceUrl: "https://cohtora.houstontx.gov/approot/soldpermits/online_permit.htm",
      additionalResourceLabel: "",
      additionalResourceUrl: "",
      status: "To Do",
      lastChecked: "",
      automated: true,
      checks: [],
    },
    {
      id: "chk-15",
      stage: "Demolition Signals",
      activity: "Asbestos & Demolition Notification (DSHS)",
      whatToLookFor:
        "DSHS must be notified at least 10 working days before any commercial or public building is demolished or abated.",
      typicalTiming: "At least 10 working days before demolition starts.",
      resourceLabel: "DSHS Notifications",
      resourceUrl: "https://vo.ras.dshs.state.tx.us/datamart/mainMenu.do",
      additionalResourceLabel: "",
      additionalResourceUrl: "",
      status: "To Do",
      lastChecked: "",
      automated: true,
      checks: [],
    },
  ];
}

/**
 * Look up a checklist row strictly by canonical row.id ("chk-01".."chk-15"), eliminating
 * fragile positional array indexing across deep-links, UI updates, and auto-sweep merges.
 */
export function findChecklistRow(checklist, checkId) {
  if (!Array.isArray(checklist) || !checkId) return null;
  const target = String(checkId).trim().toLowerCase();
  return checklist.find((r) => r && String(r.id || "").trim().toLowerCase() === target) || null;
}

function uniqueNonEmptyStrings(items = []) {
  const seen = new Set();
  const out = [];
  for (const raw of items) {
    const s = String(raw ?? "").trim();
    if (!s) continue;
    const key = s.toUpperCase();
    if (!seen.has(key)) {
      seen.add(key);
      out.push(s);
    }
  }
  return out;
}

export function normalizeSearchTerms(rawTerms, propId = "", hasExplicitTimestamp = false) {
  const base = {
    entities: [],
    dbas: [],
    officers: [],
    legalKeywords: [],
    altAddresses: [],
    projectNumbers: [],
  };

  if (
    (!rawTerms || typeof rawTerms !== "object") &&
    propId === "prop-kirby-mansion" &&
    !hasExplicitTimestamp
  ) {
    for (const cat of SEARCH_TERM_CATEGORIES) {
      base[cat.key] = uniqueNonEmptyStrings(DEFAULT_KIRBY_SEARCH_TERMS[cat.key] || []);
    }
    return base;
  }

  if (rawTerms && typeof rawTerms === "object") {
    for (const cat of SEARCH_TERM_CATEGORIES) {
      if (Array.isArray(rawTerms[cat.key])) {
        base[cat.key] = uniqueNonEmptyStrings(rawTerms[cat.key]);
      }
    }
  }
  return base;
}

/**
 * Extract structured search tokens (simplified addresses, LLCs, DBAs, officers, Clerk legal
 * Block/Subdivision tokens, HCAD IDs, and 8-digit ILMS / TABS project numbers) from a watched property.
 */
export function extractWatchPropertySearchContext(prop = {}) {
  const rawPrimary = String(prop.address || "").trim();
  const simplifiedPrimary = simplifySearchAddress(rawPrimary);

  const st = normalizeSearchTerms(
    prop.searchTerms,
    prop.id,
    Boolean(prop.searchTermsUpdatedAt)
  );

  const rawSecondaries = uniqueNonEmptyStrings([
    ...(Array.isArray(prop.secondaryAddresses) ? prop.secondaryAddresses : []),
    ...st.altAddresses,
  ]);
  const simplifiedSecondaries = uniqueNonEmptyStrings(
    rawSecondaries
      .map((a) => simplifySearchAddress(a))
      .filter((a) => a && a.toUpperCase() !== simplifiedPrimary.toUpperCase())
  );

  const splitEntities = (raw) =>
    String(raw || "")
      .split(/[,;/]+/)
      .map((s) => s.replace(/\([^)]*\)/g, "").trim())
      .filter((s) => s && !/^(?:not recorded|none|none found|unknown|n\/a)$/i.test(s));

  const ownerEntities = splitEntities(prop.ownerOfRecord);
  const buyerEntities = splitEntities(prop.buyerRelatedEntities);

  const entities = uniqueNonEmptyStrings([...st.entities, ...buyerEntities, ...ownerEntities]);
  const dbas = uniqueNonEmptyStrings(st.dbas);
  const officers = uniqueNonEmptyStrings(st.officers);

  const fullTextBlob = [
    prop.ilmsProjectNo || "",
    ...st.projectNumbers,
    prop.notes || "",
    ...((prop.checklist || []).flatMap((r) => (r.checks || []).map((c) => c.result || ""))),
  ].join(" ");

  const ilmsMatches = fullTextBlob.match(/\b(2\d{7})\b/g) || [];
  const explicitEightDigit = st.projectNumbers.filter((n) => /^\d{8}$/.test(n));
  const ilmsProjectNos = uniqueNonEmptyStrings([
    ...(prop.ilmsProjectNo && /^\d{8}$/.test(String(prop.ilmsProjectNo).trim())
      ? [String(prop.ilmsProjectNo).trim()]
      : []),
    ...explicitEightDigit,
    ...ilmsMatches,
  ]);

  const tabsMatches = fullTextBlob.match(/\b(TABS\d{8,12})\b/gi) || [];
  const explicitTabs = st.projectNumbers.filter((n) => /^TABS\d+/i.test(n));
  const tabsNos = uniqueNonEmptyStrings([...explicitTabs, ...tabsMatches]);

  const legal = extractClerkLegalCopyTerms(
    prop.legalDescription || "",
    prop.subdivisionMatch || ""
  );

  const clerkPartyTokens = [];
  const seenClerkCopies = new Set();
  for (const partyRaw of [...entities, ...officers, prop.ownerOfRecord || ""]) {
    for (const tok of formatClerkPartyTokens(partyRaw)) {
      if (tok.copy && !seenClerkCopies.has(tok.copy)) {
        seenClerkCopies.add(tok.copy);
        clerkPartyTokens.push(tok.copy);
      }
    }
  }

  const { hcadIds } = parseHcadAccountList(prop.hcadAccount || "");

  return {
    rawPrimary,
    simplifiedPrimary,
    rawSecondaries,
    simplifiedSecondaries,
    ownerEntities,
    buyerEntities,
    entities,
    dbas,
    officers,
    legal,
    legalKeywords: st.legalKeywords,
    clerkPartyTokens,
    hcadIds,
    ilmsProjectNos,
    tabsNos,
  };
}

/**
 * Build a pure, zero-DOM descriptor array of assisted portal pills and links for each of the
 * 15 checklist rows (chk-01..chk-15).
 *
 * Enforces zero street-address auto-copy fallthrough on Checks #3 (County Clerk Deeds),
 * #4 (Corporate Entity), #7 (DSHS Hemp), #8 (ILMS Plan Review), and #9 (Plat Tracker / Biweekly Plats).
 */
export function buildAssistedPortalPillsModel(prop = {}, row = {}) {
  const ctx = extractWatchPropertySearchContext(prop);
  const rowId = String(row.id || "").trim().toLowerCase();
  const pills = [];

  const pushCopy = (label, copyText, openUrl = "", extra = {}) => {
    const val = String(copyText || "").trim();
    if (!val) return;
    pills.push({
      kind: "copy",
      label,
      copyText: val,
      openUrl: openUrl ? String(openUrl) : "",
      ...extra,
    });
  };
  const pushLink = (label, href, extra = {}) => {
    const clean = String(href || "").trim();
    if (!clean) return;
    pills.push({
      kind: "link",
      label,
      href: clean,
      ...extra,
    });
  };
  const pushAction = (label, action, extra = {}) => {
    pills.push({
      kind: "action",
      label,
      action,
      ...extra,
    });
  };

  if (rowId === "chk-01") {
    const noticesUrl = row.resourceUrl || "https://www.texaspublicnotices.com/";
    pushCopy(`Copy "${ctx.simplifiedPrimary}" & Open Notices`, ctx.simplifiedPrimary, noticesUrl);
    for (const sa of ctx.simplifiedSecondaries) {
      pushCopy(`Copy "${sa}" & Open Notices`, sa, noticesUrl);
    }
    for (const term of uniqueNonEmptyStrings([...ctx.dbas, ...ctx.entities]).slice(0, 2)) {
      pushCopy(`Copy "${term}"`, term, "");
    }
  } else if (rowId === "chk-02") {
    const addrTerms = [ctx.simplifiedPrimary, ...ctx.simplifiedSecondaries]
      .filter(Boolean)
      .map((a) => `"${a}"`)
      .join(" OR ");
    if (addrTerms) {
      const marketUrl = `https://www.google.com/search?q=${encodeURIComponent(
        `(${addrTerms}) Houston (site:loopnet.com OR site:crexi.com OR site:houstonarchitecture.com)`
      )}`;
      pushLink("Search Listings & HAIF", marketUrl);
    }
    for (const sa of ctx.rawSecondaries) {
      pushLink(
        `Street View (${sa})`,
        `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(sa + ", Houston, TX")}`
      );
    }
  } else if (rowId === "chk-03") {
    // Check #3: Harris County Clerk Real Property (NO street address auto-copy)
    const clerkUrl = "https://www.cclerk.hctx.net/applications/websearch/RP.aspx";
    if (ctx.legal.block) {
      pushCopy(`Copy Block "${ctx.legal.block}" & Open HC Clerk`, ctx.legal.block, clerkUrl);
    }
    if (ctx.legal.subdivisionKeyword) {
      pushCopy(
        `Copy Subdiv "${ctx.legal.subdivisionKeyword}" & Open HC Clerk`,
        ctx.legal.subdivisionKeyword,
        clerkUrl
      );
    }
    if (
      ctx.legal.subdivisionFull &&
      ctx.legal.subdivisionFull.toUpperCase() !== ctx.legal.subdivisionKeyword.toUpperCase()
    ) {
      pushCopy(
        `Copy Full Subdiv "${ctx.legal.subdivisionFull}"`,
        ctx.legal.subdivisionFull,
        clerkUrl
      );
    }
    for (const kw of ctx.legalKeywords) {
      const cleanKw = kw.replace(/^block\s+/i, "").trim();
      const kwUpper = cleanKw.toUpperCase();
      if (
        kwUpper &&
        kwUpper !== ctx.legal.block.toUpperCase() &&
        kwUpper !== ctx.legal.subdivisionKeyword.toUpperCase()
      ) {
        pushCopy(`Copy "${cleanKw}" & Open HC Clerk`, cleanKw, clerkUrl);
      }
    }
    for (const party of ctx.clerkPartyTokens) {
      pushCopy(`Copy Party "${party}" & Open HC Clerk`, party, clerkUrl);
    }
  } else if (rowId === "chk-04") {
    // Check #4: Corporate Entity Verification (NO street address auto-copy)
    const compUrl = "https://comptroller.texas.gov/taxes/franchise/account-status/search";
    const bizaUrl = "https://www.bizapedia.com/";
    const sosUrl = "https://direct.sos.state.tx.us/acct/acct-login.asp";
    const entityTargets = uniqueNonEmptyStrings([...ctx.entities, ...ctx.officers, ...ctx.dbas]);
    for (const ent of entityTargets) {
      pushCopy(`Copy "${ent}" & Open Comptroller`, ent, compUrl);
      pushCopy(`Copy "${ent}" & Open Bizapedia`, ent, bizaUrl);
      pushLink(
        `Google "${ent}"`,
        `https://www.google.com/search?q=${encodeURIComponent(`"${ent}" Texas LLC`)}`
      );
    }
    pushLink("TX SOSDirect (Paid Portal)", sosUrl);
  } else if (rowId === "chk-05") {
    for (const id of ctx.hcadIds) {
      pushCopy(`Copy # & Open HCAD Search (${id})`, id, "https://search.hcad.org/", {
        isHcad: true,
        hcadId: id,
      });
      pushLink(
        `Open HCAD GIS Parcel Map (${id})`,
        `https://arcweb.hcad.org/parcel-viewer-v2.0/?hcad_num=${encodeURIComponent(id)}`
      );
    }
    if (ctx.simplifiedPrimary) {
      pushCopy(
        `Copy "${ctx.simplifiedPrimary}" & Open HCAD Search`,
        ctx.simplifiedPrimary,
        "https://search.hcad.org/",
        { isHcad: true }
      );
    }
  } else if (rowId === "chk-06") {
    const tabcUrl = "https://apps.tabc.texas.gov/publicinquiry/";
    pushCopy(`Copy "${ctx.simplifiedPrimary}" & Open TABC`, ctx.simplifiedPrimary, tabcUrl);
    for (const sa of ctx.simplifiedSecondaries) {
      pushCopy(`Copy "${sa}" & Open TABC`, sa, tabcUrl);
    }
    for (const term of uniqueNonEmptyStrings([...ctx.dbas, ...ctx.entities])) {
      pushCopy(`Copy "${term}" & Open TABC`, term, tabcUrl);
    }
  } else if (rowId === "chk-07") {
    // Check #7: DSHS Hemp License Search (name-based only, NO street address auto-copy)
    const dshsHempUrl = "https://vo.ras.dshs.state.tx.us/datamart/login.do";
    const nameTargets = uniqueNonEmptyStrings([...ctx.entities, ...ctx.dbas, ...ctx.officers]);
    for (const nm of nameTargets) {
      pushCopy(`Copy "${nm}" & Open DSHS Hemp`, nm, dshsHempUrl);
    }
    if (nameTargets.length === 0) {
      pushAction("+ Add Entity / DBA Search Term", "focus-snowball-entity");
    }
  } else if (rowId === "chk-08") {
    // Check #8: Pending Plans / GovQA / ILMS (8-digit Project # only, NO street address auto-copy)
    const ilmsUrl = "https://www.pdinet.pd.houstontx.gov/cohilms/webs/plan_lookup.asp";
    pushAction("Draft Ready-to-Send GovQA Request", "open-govqa-modal", { extraClass: "govqa" });
    for (const pn of ctx.ilmsProjectNos) {
      pushCopy(`Copy ILMS #${pn} & Open Plan Lookup`, pn, ilmsUrl);
    }
    pushLink(
      "Find Project # in Houston Sold Permits",
      "https://cohtora.houstontx.gov/approot/soldpermits/online_permit.htm"
    );
    pushAction("+ Add 8-Digit Project #", "focus-snowball-project");
  } else if (rowId === "chk-09") {
    // Check #9: Replats & Variances (Biweekly Plat Monitor + COH dev_reports, NO plattracker SignIn, NO address copy)
    pushAction("Open Biweekly Plat & Variance Review Tab", "switch-to-agenda");
    const platTerms = uniqueNonEmptyStrings([
      prop.platProjectName || "",
      ctx.legal.subdivisionKeyword || "",
      prop.subdivisionMatch || "",
    ]);
    for (const term of platTerms) {
      pushCopy(`Copy Plat/Subdiv "${term}"`, term, "");
    }
  } else if (rowId === "chk-10") {
    // Check #10: TDLR TABS (https://www.tdlr.texas.gov/tabs/search + DBAs + simplified addresses)
    const tabsUrl = "https://www.tdlr.texas.gov/tabs/search";
    for (const tn of ctx.tabsNos) {
      pushCopy(`Copy ${tn} & Open TABS`, tn, tabsUrl);
    }
    for (const dba of ctx.dbas) {
      pushCopy(`Copy Facility "${dba}" & Open TABS`, dba, tabsUrl);
    }
    pushCopy(`Copy "${ctx.simplifiedPrimary}" & Open TABS`, ctx.simplifiedPrimary, tabsUrl);
    for (const sa of ctx.simplifiedSecondaries) {
      pushCopy(`Copy "${sa}" & Open TABS`, sa, tabsUrl);
    }
    for (const ent of ctx.entities) {
      pushCopy(`Copy Owner "${ent}" & Open TABS`, ent, tabsUrl);
    }
  } else if (rowId === "chk-11") {
    // Check #11: City Secretary NovusAgenda
    const novusUrl = "https://houston.novusagenda.com/agendapublic/";
    pushCopy(`Copy "${ctx.simplifiedPrimary}" & Open NovusAgenda`, ctx.simplifiedPrimary, novusUrl);
    for (const sa of ctx.simplifiedSecondaries) {
      pushCopy(`Copy "${sa}" & Open NovusAgenda`, sa, novusUrl);
    }
    if (ctx.legal.subdivisionKeyword) {
      pushCopy(
        `Copy "${ctx.legal.subdivisionKeyword}" & Open NovusAgenda`,
        ctx.legal.subdivisionKeyword,
        novusUrl
      );
    }
  } else if (rowId === "chk-12") {
    const tceqUrl = "https://www.tceq.texas.gov/permitting/central_registry";
    pushCopy(`Copy "${ctx.simplifiedPrimary}" & Open TCEQ`, ctx.simplifiedPrimary, tceqUrl);
    for (const sa of ctx.simplifiedSecondaries) {
      pushCopy(`Copy "${sa}" & Open TCEQ`, sa, tceqUrl);
    }
    if (ctx.entities[0]) {
      pushCopy(`Copy "${ctx.entities[0]}" & Open TCEQ`, ctx.entities[0], tceqUrl);
    }
  } else if (rowId === "chk-13") {
    const sr311Url = "https://experience.arcgis.com/experience/cf006ed734814cee9f5b9bcf7abc839f";
    pushAction("Import & Cross-Check Dangerous Buildings CSV", "open-dondb-modal", {
      extraClass: "govqa",
    });
    pushCopy(`Copy "${ctx.simplifiedPrimary}" & Open 311 Map`, ctx.simplifiedPrimary, sr311Url);
    for (const sa of ctx.simplifiedSecondaries) {
      pushCopy(`Copy "${sa}" & Open 311 Map`, sa, sr311Url);
    }
  } else if (rowId === "chk-14") {
    // Check #14: Houston Sold Permits only (weekly Permit Activity Reports removed)
    const soldUrl = "https://cohtora.houstontx.gov/approot/soldpermits/online_permit.htm";
    pushCopy(`Copy "${ctx.simplifiedPrimary}" & Open Sold Permits`, ctx.simplifiedPrimary, soldUrl);
    for (const sa of ctx.simplifiedSecondaries) {
      pushCopy(`Copy "${sa}" & Open Sold Permits`, sa, soldUrl);
    }
  } else if (rowId === "chk-15") {
    const dshsUrl = "https://vo.ras.dshs.state.tx.us/datamart/mainMenu.do";
    pushCopy(`Copy "${ctx.simplifiedPrimary}" & Open DSHS`, ctx.simplifiedPrimary, dshsUrl);
    for (const sa of ctx.simplifiedSecondaries) {
      pushCopy(`Copy "${sa}" & Open DSHS`, sa, dshsUrl);
    }
    if (ctx.entities[0]) {
      pushCopy(`Copy "${ctx.entities[0]}" & Open DSHS`, ctx.entities[0], dshsUrl);
    }
  }

  return pills;
}

export function isAutoSweepFinding(checkEntry) {
  if (!checkEntry || typeof checkEntry !== "object") return false;
  if (checkEntry.source === "auto-sweep") return true;
  return /^\[AUTO-SWEEP\]/i.test(String(checkEntry.result || "").trim());
}

export function isAdverseAutoSweepFinding(checkEntry) {
  if (!isAutoSweepFinding(checkEntry)) return false;
  if (typeof checkEntry.adverse === "boolean") return checkEntry.adverse;
  const text = String(checkEntry.result || "");
  if (
    /\b(?:no new|0 active|no matching|none found|confirmed match to current owner)\b/i.test(text)
  ) {
    return false;
  }
  return true;
}

export function getAutoSweepDedupeKey(rowId, checkEntry) {
  const rid = String(rowId || "").trim().toLowerCase();
  if (checkEntry?.agencyRecordId) {
    return `${rid}::id:${String(checkEntry.agencyRecordId).trim()}`;
  }
  const normText = String(checkEntry?.result || "")
    .trim()
    .replace(/\s+/g, " ")
    .toUpperCase();
  return `${rid}::txt:${normText}`;
}

/**
 * Rebuild chronological findingsLog from all 15 checklist rows, preserving sanitized links.
 */
export function rebuildFindingsLog(prop) {
  if (!prop || !Array.isArray(prop.checklist)) return [];
  const log = [];
  prop.checklist.forEach((row, idxRow) => {
    (row.checks || []).forEach((chk, idxChk) => {
      const cleanLinks = sanitizeExternalLinksList(chk.links);
      const entry = {
        date: chk.date || "",
        stage: row.stage,
        activity: row.activity,
        checkId: row.id || "",
        result: chk.result || "",
        source: isAutoSweepFinding(chk) ? "auto-sweep" : "manual",
        automated: isAutoSweepFinding(chk) || Boolean(row.automated),
        links: cleanLinks,
        _order: `${chk.date || "9999-99-99"}-${String(idxRow).padStart(2, "0")}-${String(
          idxChk
        ).padStart(3, "0")}`,
      };
      if (chk.discoveredAt) entry.discoveredAt = String(chk.discoveredAt);
      if (chk.loggedAt) entry.loggedAt = String(chk.loggedAt);
      if (chk.agencyRecordId) entry.agencyRecordId = String(chk.agencyRecordId);
      if (typeof chk.adverse === "boolean") entry.adverse = chk.adverse;
      log.push(entry);
    });
  });
  log.sort((a, b) => a._order.localeCompare(b._order));
  log.forEach((e, i) => {
    e.entryNo = i + 1;
    delete e._order;
  });
  prop.findingsLog = log;
  return log;
}

/**
 * Normalize a watched property object from static JSON, localStorage, or Google Sheet sync,
 * reconciling legacy aliases (architectEngineer <-> designFirm, documents <-> driveLinks,
 * name/title <-> propertyName) and upgrading all 15 checklist rows by row.id ("chk-01".."chk-15").
 */
export function normalizeWatchProperty(rawProp = {}) {
  if (!rawProp || typeof rawProp !== "object") return null;

  const id = String(rawProp.id || "").trim() || `prop-${Date.now()}`;
  const address = String(rawProp.address || "").trim();
  const propertyName = String(
    rawProp.propertyName || rawProp.name || rawProp.title || address || id
  ).trim();
  const designFirm = String(rawProp.designFirm ?? rawProp.architectEngineer ?? "").trim();
  const tierRaw = String(rawProp.tier || "1").trim();
  const tier = ["1", "2", "3"].includes(tierRaw) ? tierRaw : "1";

  const defaultStatus =
    tier === "3"
      ? "Archived (Tier 3)"
      : tier === "2"
      ? "Monitoring"
      : "Active / High Priority";
  const rawTrackerStatus = String(rawProp.trackerStatus || "").trim();
  const trackerStatus =
    rawTrackerStatus === REMOVED_TIER3_TOMBSTONE
      ? REMOVED_TIER3_TOMBSTONE
      : TRACKER_STATUSES.includes(rawTrackerStatus)
      ? rawTrackerStatus
      : defaultStatus;

  const rawThreat = String(rawProp.threatCategory || "").trim();
  const threatCategory = rawThreat && THREAT_CATEGORIES.includes(rawThreat) ? rawThreat : "";

  const rawDriveLinks = Array.isArray(rawProp.driveLinks)
    ? rawProp.driveLinks
    : Array.isArray(rawProp.documents)
    ? rawProp.documents
    : [];
  const driveLinks = sanitizeExternalLinksList(rawDriveLinks);

  const searchTerms = normalizeSearchTerms(
    rawProp.searchTerms,
    id,
    Boolean(rawProp.searchTermsUpdatedAt)
  );

  // Merge incoming checklist strictly by row.id ("chk-01".."chk-15") onto canonical template
  const templateRows = buildDefaultChecklist(address || propertyName);
  const incomingList = Array.isArray(rawProp.checklist) ? rawProp.checklist : [];

  const checklist = templateRows.map((tpl, idx) => {
    const matched =
      findChecklistRow(incomingList, tpl.id) ||
      (!incomingList[idx]?.id && incomingList[idx] ? incomingList[idx] : null);
    if (!matched) return tpl;

    const checks = (Array.isArray(matched.checks) ? matched.checks : []).map((c) => {
      const entry = {
        date: String(c?.date || ""),
        result: String(c?.result || ""),
      };
      if (c?.source) entry.source = String(c.source);
      if (c?.discoveredAt) entry.discoveredAt = String(c.discoveredAt);
      if (c?.loggedAt) entry.loggedAt = String(c.loggedAt);
      if (c?.agencyRecordId) entry.agencyRecordId = String(c.agencyRecordId);
      if (typeof c?.adverse === "boolean") entry.adverse = c.adverse;
      const cleanLinks = sanitizeExternalLinksList(c?.links);
      if (cleanLinks.length > 0) entry.links = cleanLinks;
      return entry;
    });

    const outRow = {
      ...tpl,
      status: ["To Do", "In Progress", "Flagged", "Complete", "N/A"].includes(matched.status)
        ? matched.status
        : tpl.status,
      lastChecked: String(matched.lastChecked || ""),
      checks,
    };
    if (matched.updatedAt) outRow.updatedAt = String(matched.updatedAt);
    if (typeof matched.statusChangedAt === "string") outRow.statusChangedAt = matched.statusChangedAt;
    if (matched.lastAutoSweepAt) outRow.lastAutoSweepAt = String(matched.lastAutoSweepAt);
    if (matched.humanStatusEdited === true) outRow.humanStatusEdited = true;
    if (
      tpl.id === "chk-15" &&
      String(matched.additionalResourceUrl || "").includes("notificationPrintAction.do")
    ) {
      outRow.additionalResourceLabel = String(
        matched.additionalResourceLabel || "DSHS Notification"
      );
      outRow.additionalResourceUrl = String(matched.additionalResourceUrl);
    }
    return outRow;
  });

  const rawKd = rawProp.keyDates && typeof rawProp.keyDates === "object" ? rawProp.keyDates : {};
  const keyDates = {
    monitoringStarted: String(rawKd.monitoringStarted || ""),
    targetClosing: String(rawKd.targetClosing || ""),
    planningCommission: String(rawKd.planningCommission || ""),
    publicHearingDate: String(rawKd.publicHearingDate || ""),
    asbestosAbatementWindow: String(rawKd.asbestosAbatementWindow || ""),
  };

  const resolvedAppNo = String(
    rawProp.appNo || rawProp.sourcePlatApp || (id.startsWith("plat-") ? id.slice(5) : "")
  ).trim();

  const normalized = {
    id,
    propertyName,
    platProjectName: String(
      rawProp.platProjectName || (resolvedAppNo ? propertyName : "") || ""
    ).trim(),
    address,
    secondaryAddresses: uniqueNonEmptyStrings(
      Array.isArray(rawProp.secondaryAddresses) ? rawProp.secondaryAddresses : []
    ),
    hcadAccount: String(rawProp.hcadAccount ?? rawProp.hcad ?? rawProp.hcad_num ?? "").trim(),
    legalDescription: String(rawProp.legalDescription || "").trim(),
    buildingId: String(rawProp.buildingId || "").trim(),
    yearBuilt: rawProp.yearBuilt ?? "",
    yearRemodeled: rawProp.yearRemodeled ?? "",
    architect: String(rawProp.architect || "").trim(),
    designation: String(rawProp.designation || "").trim(),
    ownerOfRecord: String(rawProp.ownerOfRecord ?? rawProp.owner ?? "").trim(),
    buyerRelatedEntities: String(rawProp.buyerRelatedEntities || "").trim(),
    lender: String(rawProp.lender || "").trim(),
    designFirm,
    architectEngineer: designFirm,
    tier,
    tierConfirmed: rawProp.tierConfirmed !== false,
    threatCategory,
    trackerStatus,
    neighborhood: String(
      rawProp.neighborhood || rawProp.superNeighborhoodMatch || ""
    ).trim(),
    reportedBy: String(rawProp.reportedBy || "").trim(),
    phPastActions: String(rawProp.phPastActions || "").trim(),
    ilmsProjectNo: String(rawProp.ilmsProjectNo || "").trim(),
    keyDates,
    notes: String(rawProp.notes || ""),
    lng: rawProp.lng ?? null,
    lat: rawProp.lat ?? null,
    coordSource: String(rawProp.coordSource || ""),
    subdivisionMatch: String(rawProp.subdivisionMatch || ""),
    subdivisionId: String(rawProp.subdivisionId || ""),
    superNeighborhoodMatch: String(rawProp.superNeighborhoodMatch || ""),
    superNeighborhoodId: String(rawProp.superNeighborhoodId || ""),
    appNo: resolvedAppNo,
    sourcePlatApp: resolvedAppNo,
    section: String(rawProp.section || ""),
    source: String(rawProp.source || ""),
    sourceCycleKey: String(rawProp.sourceCycleKey || ""),
    pcMeetingDate: String(rawProp.pcMeetingDate || ""),
    hearingDateIso: String(rawProp.hearingDateIso || ""),
    platPdf: String(rawProp.platPdf || ""),
    noticePdf: String(rawProp.noticePdf || ""),
    continuityKey: String(rawProp.continuityKey || ""),
    priorCycles: Array.isArray(rawProp.priorCycles) ? rawProp.priorCycles : [],
    searchTerms,
    searchTermsUpdatedAt: String(rawProp.searchTermsUpdatedAt || ""),
    driveLinks,
    documents: driveLinks,
    driveLinksUpdatedAt: String(rawProp.driveLinksUpdatedAt || ""),
    lastAutoSweepAt: String(rawProp.lastAutoSweepAt || ""),
    autoSweepSchedule: String(rawProp.autoSweepSchedule || "Daily at 7:00 AM CT"),
    autoSweepSummary:
      rawProp.autoSweepSummary && typeof rawProp.autoSweepSummary === "object"
        ? rawProp.autoSweepSummary
        : null,
    humanEdited: Boolean(rawProp.humanEdited),
    archivedAt: String(rawProp.archivedAt || ""),
    updatedAt: String(rawProp.updatedAt || ""),
    updatedBy: String(rawProp.updatedBy || ""),
    checklist,
  };

  rebuildFindingsLog(normalized);
  return normalized;
}

/**
 * Serialize a watched property into a lossless payload for Plat_&_Watchlist_Sync and localStorage.
 */
export function serializeWatchPropertyForSync(prop, updatedBy = "", nowIso = "") {
  const norm = normalizeWatchProperty(prop);
  if (!norm) return null;
  const ts = nowIso || norm.updatedAt || new Date().toISOString();
  return {
    ...norm,
    designFirm: norm.designFirm,
    architectEngineer: norm.designFirm,
    documents: norm.driveLinks,
    updatedAt: ts,
    updatedBy: updatedBy || norm.updatedBy || "",
  };
}

function parseIsoMs(ts = "") {
  if (!ts) return 0;
  const ms = Date.parse(String(ts));
  return Number.isFinite(ms) ? ms : 0;
}

/**
 * Ownership-partitioned 3-way Watchlist state reconciliation across:
 *   1. staticProp (property_watchlist.json with daily 7:00 AM [AUTO-SWEEP] findings)
 *   2. cloudProp  (Plat_&_Watchlist_Sync Google Sheet CSV row)
 *   3. localProp  (browser localStorage)
 *
 * Guarantees:
 *   - Fresh [AUTO-SWEEP] findings union-merge idempotently by row.id ("chk-01".."chk-15") even
 *     when cloud CSV or localStorage holds an older checklist snapshot.
 *   - Human-edited metadata, deleted snowball terms, and deleted Drive links follow monotonic
 *     updatedAt timestamps so deleted items never resurrect.
 *   - Manual "Complete" / "In Progress" / "N/A" row statuses survive non-adverse sweeps, escalating
 *     to "Flagged" only when an unseen adverse [AUTO-SWEEP] hit arrives after human lastChecked.
 */
export function reconcileWatchlistProperty(staticProp, cloudProp, localProp) {
  const sNorm = staticProp ? normalizeWatchProperty(staticProp) : null;
  const cNorm = cloudProp ? normalizeWatchProperty(cloudProp) : null;
  const lNorm = localProp ? normalizeWatchProperty(localProp) : null;

  const candidates = [sNorm, cNorm, lNorm].filter(Boolean);
  if (candidates.length === 0) return null;

  // Rank human-edit snapshots: explicit updatedAt wins; ties favor cloud > local > static
  const rankFor = (p, tsField = "updatedAt", tieBonus = 0) => {
    if (!p) return -1;
    const ms = parseIsoMs(p[tsField] || p.updatedAt);
    return ms > 0 ? ms * 10 + tieBonus : tieBonus;
  };

  const humanWinner = [
    { p: sNorm, r: rankFor(sNorm, "updatedAt", 1) },
    { p: lNorm, r: rankFor(lNorm, "updatedAt", 2) },
    { p: cNorm, r: rankFor(cNorm, "updatedAt", 3) },
  ]
    .filter((x) => x.p)
    .sort((a, b) => b.r - a.r)[0].p;

  if (humanWinner.trackerStatus === REMOVED_TIER3_TOMBSTONE) {
    return null;
  }

  const hasExplicitSearchTerms = (raw) =>
    Boolean((raw && raw.searchTerms && typeof raw.searchTerms === "object") || raw?.searchTermsUpdatedAt);
  const hasExplicitDriveLinks = (raw) =>
    Boolean(Array.isArray(raw?.driveLinks) || Array.isArray(raw?.documents) || raw?.driveLinksUpdatedAt);

  const explicitTermsCandidates = [
    hasExplicitSearchTerms(staticProp) ? { p: sNorm, r: rankFor(sNorm, "searchTermsUpdatedAt", 1) } : null,
    hasExplicitSearchTerms(localProp) ? { p: lNorm, r: rankFor(lNorm, "searchTermsUpdatedAt", 2) } : null,
    hasExplicitSearchTerms(cloudProp) ? { p: cNorm, r: rankFor(cNorm, "searchTermsUpdatedAt", 3) } : null,
  ]
    .filter((x) => x && x.p)
    .sort((a, b) => b.r - a.r);

  const termsWinner =
    explicitTermsCandidates.length > 0
      ? explicitTermsCandidates[0].p
      : [
          { p: sNorm, r: rankFor(sNorm, "searchTermsUpdatedAt", 1) },
          { p: lNorm, r: rankFor(lNorm, "searchTermsUpdatedAt", 2) },
          { p: cNorm, r: rankFor(cNorm, "searchTermsUpdatedAt", 3) },
        ]
          .filter((x) => x.p)
          .sort((a, b) => b.r - a.r)[0].p;

  const explicitLinksCandidates = [
    hasExplicitDriveLinks(staticProp) ? { p: sNorm, r: rankFor(sNorm, "driveLinksUpdatedAt", 1) } : null,
    hasExplicitDriveLinks(localProp) ? { p: lNorm, r: rankFor(lNorm, "driveLinksUpdatedAt", 2) } : null,
    hasExplicitDriveLinks(cloudProp) ? { p: cNorm, r: rankFor(cNorm, "driveLinksUpdatedAt", 3) } : null,
  ]
    .filter((x) => x && x.p)
    .sort((a, b) => b.r - a.r);

  const linksWinner =
    explicitLinksCandidates.length > 0
      ? explicitLinksCandidates[0].p
      : [
          { p: sNorm, r: rankFor(sNorm, "driveLinksUpdatedAt", 1) },
          { p: lNorm, r: rankFor(lNorm, "driveLinksUpdatedAt", 2) },
          { p: cNorm, r: rankFor(cNorm, "driveLinksUpdatedAt", 3) },
        ]
          .filter((x) => x.p)
          .sort((a, b) => b.r - a.r)[0].p;

  const sweepWinner = [sNorm, cNorm, lNorm]
    .filter(Boolean)
    .sort((a, b) => parseIsoMs(b.lastAutoSweepAt) - parseIsoMs(a.lastAutoSweepAt))[0];

  const base = normalizeWatchProperty(humanWinner);

  // Enrich spatial/coordinate fields from static pipeline output when missing on human snapshot
  if (sNorm) {
    if (!base.lat && sNorm.lat) base.lat = sNorm.lat;
    if (!base.lng && sNorm.lng) base.lng = sNorm.lng;
    if (!base.coordSource && sNorm.coordSource) base.coordSource = sNorm.coordSource;
    if (!base.buildingId && sNorm.buildingId) base.buildingId = sNorm.buildingId;
    if (!base.subdivisionMatch && sNorm.subdivisionMatch) {
      base.subdivisionMatch = sNorm.subdivisionMatch;
    }
    if (!base.subdivisionId && sNorm.subdivisionId) base.subdivisionId = sNorm.subdivisionId;
    if (!base.superNeighborhoodMatch && sNorm.superNeighborhoodMatch) {
      base.superNeighborhoodMatch = sNorm.superNeighborhoodMatch;
    }
    if (!base.superNeighborhoodId && sNorm.superNeighborhoodId) {
      base.superNeighborhoodId = sNorm.superNeighborhoodId;
    }
    if (!base.pcMeetingDate && sNorm.pcMeetingDate) base.pcMeetingDate = sNorm.pcMeetingDate;
    if (!base.sourceCycleKey && sNorm.sourceCycleKey) base.sourceCycleKey = sNorm.sourceCycleKey;
    if (!base.hearingDateIso && sNorm.hearingDateIso) base.hearingDateIso = sNorm.hearingDateIso;
  }

  base.searchTerms = JSON.parse(JSON.stringify(termsWinner.searchTerms));
  base.searchTermsUpdatedAt = termsWinner.searchTermsUpdatedAt || termsWinner.updatedAt || "";

  // Preserve newly harvested 8-digit projectNumbers from newer automated pipeline sweeps
  if (
    sNorm &&
    termsWinner !== sNorm &&
    parseIsoMs(sNorm.lastAutoSweepAt) >
      parseIsoMs(termsWinner.searchTermsUpdatedAt || termsWinner.updatedAt)
  ) {
    base.searchTerms.projectNumbers = uniqueNonEmptyStrings([
      ...(base.searchTerms.projectNumbers || []),
      ...(sNorm.searchTerms?.projectNumbers || []),
    ]);
  }

  base.driveLinks = sanitizeExternalLinksList(linksWinner.driveLinks);
  base.documents = base.driveLinks;
  base.driveLinksUpdatedAt = linksWinner.driveLinksUpdatedAt || linksWinner.updatedAt || "";

  if (sweepWinner?.lastAutoSweepAt) {
    base.lastAutoSweepAt = sweepWinner.lastAutoSweepAt;
    base.autoSweepSchedule = sweepWinner.autoSweepSchedule || base.autoSweepSchedule;
    base.autoSweepSummary = sweepWinner.autoSweepSummary || base.autoSweepSummary;
  }

  // Reconcile all 15 checklist rows strictly by canonical row.id ("chk-01".."chk-15")
  const templateRows = buildDefaultChecklist(base.address || base.propertyName);
  base.checklist = templateRows.map((tpl) => {
    const sRow = findChecklistRow(sNorm?.checklist, tpl.id);
    const lRow = findChecklistRow(lNorm?.checklist, tpl.id);
    const cRow = findChecklistRow(cNorm?.checklist, tpl.id);

    const rowRank = (row, parent, bonus) => {
      if (!row) return -1;
      const ms = parseIsoMs(row.updatedAt || parent?.updatedAt);
      return ms > 0 ? ms * 10 + bonus : bonus;
    };

    const rowHumanWinner = [
      { row: sRow, r: rowRank(sRow, sNorm, 1) },
      { row: lRow, r: rowRank(lRow, lNorm, 2) },
      { row: cRow, r: rowRank(cRow, cNorm, 3) },
    ]
      .filter((x) => x.row)
      .sort((a, b) => b.r - a.r)[0]?.row;

    const humanStatus = rowHumanWinner?.status || "To Do";
    const humanLastChecked = String(rowHumanWinner?.lastChecked || "");
    const humanUpdatedAt = String(rowHumanWinner?.updatedAt || "");

    // Collect manual checks from winning human row + any non-duplicate manual checks,
    // retaining earliest explicit loggedAt timestamp when an identical manual entry appears across sources.
    const manualChecks = [];
    const manualByKey = new Map();
    const orderedManualSources = [rowHumanWinner, cRow, lRow, sRow].filter(Boolean);
    let lockedManualList = false;
    for (const srcRow of orderedManualSources) {
      for (const chk of srcRow.checks || []) {
        if (isAutoSweepFinding(chk)) continue;
        const mKey = `${chk.date || ""}::${String(chk.result || "").trim()}`;
        if (!manualByKey.has(mKey)) {
          if (lockedManualList) continue;
          const entry = {
            ...chk,
            ...(chk.links ? { links: sanitizeExternalLinksList(chk.links) } : {}),
          };
          manualByKey.set(mKey, entry);
          manualChecks.push(entry);
        } else if (chk.loggedAt) {
          const existing = manualByKey.get(mKey);
          if (!existing.loggedAt || String(chk.loggedAt) < String(existing.loggedAt)) {
            existing.loggedAt = String(chk.loggedAt);
          }
        }
      }
      // If winning human row carries an explicit row.updatedAt, treat its manual check list as authoritative
      // while still backfilling loggedAt metadata on matching entries from sibling snapshots.
      if (srcRow === rowHumanWinner && rowHumanWinner?.updatedAt) {
        lockedManualList = true;
      }
    }

    // Union-merge all machine [AUTO-SWEEP] checks across static, cloud, and local idempotently,
    // retaining the earliest discoveredAt when an identical finding exists across sources.
    const autoByKey = new Map();
    for (const srcRow of [sRow, cRow, lRow].filter(Boolean)) {
      for (const chk of srcRow.checks || []) {
        if (!isAutoSweepFinding(chk)) continue;
        const aKey = getAutoSweepDedupeKey(tpl.id, chk);
        if (!autoByKey.has(aKey)) {
          autoByKey.set(aKey, {
            ...chk,
            source: "auto-sweep",
            ...(chk.links ? { links: sanitizeExternalLinksList(chk.links) } : {}),
          });
        } else {
          const prevEntry = autoByKey.get(aKey);
          const incDisc = String(chk.discoveredAt || "");
          const prevDisc = String(prevEntry.discoveredAt || "");
          if (incDisc && (!prevDisc || incDisc < prevDisc)) {
            prevEntry.discoveredAt = incDisc;
          }
        }
      }
    }
    const autoChecks = Array.from(autoByKey.values());

    const combinedChecks = [...manualChecks, ...autoChecks].sort((a, b) =>
      String(a.date || "").localeCompare(String(b.date || ""))
    );

    // Keep row.lastChecked strictly for human check/acknowledgment dates (relying on
    // row.lastAutoSweepAt and chk.discoveredAt for machine sweeps) so backdated adverse
    // filings escalate to "Flagged" while manual "Complete"/"In Progress"/"N/A" resolutions persist.
    const isHumanAcknowledged = Boolean(
      rowHumanWinner?.humanStatusEdited === true ||
        humanStatus === "Complete" ||
        humanStatus === "N/A"
    );
    const humanAckDate = isHumanAcknowledged
      ? humanLastChecked || (humanUpdatedAt ? humanUpdatedAt.slice(0, 10) : "")
      : humanLastChecked;

    let nextStatus = humanStatus;
    const hasUnseenAdverseAutoHit = autoChecks.some((chk) => {
      if (!isAdverseAutoSweepFinding(chk)) return false;
      const effectiveHitDate =
        [String(chk.discoveredAt || ""), String(chk.date || "")]
          .filter(Boolean)
          .sort()
          .pop() || "";
      if (!humanAckDate) return true;
      return effectiveHitDate > humanAckDate;
    });

    if (hasUnseenAdverseAutoHit && humanStatus !== "N/A") {
      nextStatus = "Flagged";
    } else if (humanStatus === "To Do" && autoChecks.length > 0 && !hasUnseenAdverseAutoHit) {
      nextStatus = "In Progress";
    }

    const latestRowSweep =
      [sRow, cRow, lRow]
        .map((r) => String(r?.lastAutoSweepAt || ""))
        .filter(Boolean)
        .sort()
        .pop() || "";

    const mergedRow = {
      ...tpl,
      status: nextStatus,
      lastChecked: humanLastChecked,
      checks: combinedChecks,
    };
    if (humanUpdatedAt) mergedRow.updatedAt = humanUpdatedAt;
    if (typeof rowHumanWinner?.statusChangedAt === "string") {
      mergedRow.statusChangedAt = rowHumanWinner.statusChangedAt;
    }
    if (rowHumanWinner?.humanStatusEdited === true) mergedRow.humanStatusEdited = true;
    if (latestRowSweep) mergedRow.lastAutoSweepAt = latestRowSweep;
    return mergedRow;
  });

  rebuildFindingsLog(base);
  return base;
}

/**
 * Synchronize a tier change (including Tier 1/2 -> Tier 3 downgrade) from Biweekly Plat Review
 * onto the Property Watchlist while protecting human-authored dossier research via soft-archiving.
 */
export function syncPlatTierToWatchlist(
  watchedProperties = [],
  { appNo = "", hcadAccount = "", newTier = "", nowIso = "" } = {}
) {
  const list = (Array.isArray(watchedProperties) ? watchedProperties : []).map((p) =>
    normalizeWatchProperty(p)
  );
  const ts = nowIso || new Date().toISOString();
  const cleanApp = String(appNo || "").trim();
  const { hcadIds } = parseHcadAccountList(hcadAccount || "");

  const idx = list.findIndex((p) => {
    if (!p) return false;
    if (cleanApp && (p.appNo === cleanApp || p.id === `plat-${cleanApp}`)) return true;
    if (hcadIds.length > 0) {
      const pHcads = parseHcadAccountList(p.hcadAccount || "").hcadIds;
      if (pHcads.some((id) => hcadIds.includes(id)) && (p.appNo || p.id.startsWith("plat-"))) {
        return true;
      }
    }
    return false;
  });

  if (idx < 0) {
    return { watchedProperties: list, action: "none", targetProp: null };
  }

  const target = list[idx];
  if (newTier === "3" || newTier === "") {
    if (hasHumanDossierContributions(target)) {
      target.tier = "3";
      target.trackerStatus = "Archived (Tier 3)";
      target.archivedAt = ts;
      target.updatedAt = ts;
      return { watchedProperties: list, action: "archived", targetProp: target };
    }
    list.splice(idx, 1);
    return { watchedProperties: list, action: "removed", targetProp: target };
  }

  if (newTier === "1" || newTier === "2") {
    target.tier = newTier;
    target.tierConfirmed = true;
    if (target.trackerStatus === "Archived (Tier 3)") {
      target.trackerStatus = newTier === "1" ? "Active / High Priority" : "Monitoring";
      target.archivedAt = "";
    }
    target.updatedAt = ts;
    return { watchedProperties: list, action: "updated", targetProp: target };
  }

  return { watchedProperties: list, action: "none", targetProp: target };
}

/**
 * Sort Watchlist properties strictly Tier 1 -> Tier 2 -> Tier 3, keeping Archived (Tier 3)
 * entries at the bottom when shown.
 */
export function sortWatchedProperties(list = []) {
  const tierOrder = { "1": 0, "2": 1, "3": 2 };
  return (Array.isArray(list) ? list : []).slice().sort((a, b) => {
    const aArch = a?.trackerStatus === "Archived (Tier 3)" ? 1 : 0;
    const bArch = b?.trackerStatus === "Archived (Tier 3)" ? 1 : 0;
    if (aArch !== bArch) return aArch - bArch;
    const ta = tierOrder[String(a?.tier || "1")] ?? 2;
    const tb = tierOrder[String(b?.tier || "1")] ?? 2;
    if (ta !== tb) return ta - tb;
    const aKirby = a?.id === "prop-kirby-mansion" ? 0 : 1;
    const bKirby = b?.id === "prop-kirby-mansion" ? 0 : 1;
    if (aKirby !== bKirby) return aKirby - bKirby;
    const addrA = String(a?.address || a?.propertyName || "");
    const addrB = String(b?.address || b?.propertyName || "");
    return addrA.localeCompare(addrB);
  });
}

/* ---------- Dangerous Buildings (DONDB*.csv) 4-Stage Countywide Cross-Checker ---------- */

export function parseCsvRfc4180(csvText = "") {
  const text = String(csvText || "").replace(/^\uFEFF/, "");
  const rows = [];
  let row = [];
  let cell = "";
  let inQuotes = false;

  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          cell += '"';
          i += 1;
        } else {
          inQuotes = false;
        }
      } else {
        cell += ch;
      }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === ",") {
      row.push(cell);
      cell = "";
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i += 1;
      row.push(cell);
      if (row.some((c) => String(c).trim() !== "")) {
        rows.push(row);
      }
      row = [];
      cell = "";
    } else {
      cell += ch;
    }
  }
  if (cell !== "" || row.length > 0) {
    row.push(cell);
    if (row.some((c) => String(c).trim() !== "")) {
      rows.push(row);
    }
  }
  return rows;
}

export function parseCloudSheetSyncRows(csvText = "") {
  const rows = parseCsvRfc4180(csvText);
  if (rows.length < 2) {
    return { platReviews: [], watchProperties: [] };
  }
  const hdr = rows[0].map((h) => String(h || "").trim().toLowerCase());
  const idxStatus = hdr.indexOf("status");
  const idxHcad = hdr.indexOf("hcad_num");
  const idxYr = hdr.indexOf("year_built");
  const idxDesig = hdr.indexOf("historic_district");
  const idxTier = hdr.indexOf("contributing");
  const idxType = hdr.indexOf("source_type");
  const idxCit = hdr.indexOf("source_citation");
  const idxVer = hdr.indexOf("verified_by");
  if (idxHcad < 0 || idxCit < 0) {
    return { platReviews: [], watchProperties: [] };
  }

  const platReviews = [];
  const watchProperties = [];

  for (let i = 1; i < rows.length; i += 1) {
    const row = rows[i];
    const st = idxStatus >= 0 ? String(row[idxStatus] || "").trim().toLowerCase() : "approved";
    if (st && st !== "approved") continue;
    const key = String(row[idxHcad] || "").trim();
    const sType = idxType >= 0 ? String(row[idxType] || "").trim().toUpperCase() : "";
    const rawJson = String(row[idxCit] || "").trim();
    if (!rawJson.startsWith("{")) continue;

    let parsed = null;
    try {
      parsed = JSON.parse(rawJson);
    } catch (_e) {
      continue;
    }
    if (!parsed || typeof parsed !== "object") continue;

    const sheetYr = idxYr >= 0 ? String(row[idxYr] || "").trim() : "";
    const sheetDesig = idxDesig >= 0 ? String(row[idxDesig] || "").trim() : "";
    const sheetTierRaw = idxTier >= 0 ? String(row[idxTier] || "").trim() : "";
    const sheetTierMatch = sheetTierRaw.match(/([123])/);
    const sheetTier = sheetTierMatch ? sheetTierMatch[1] : "";
    const sheetReviewer = idxVer >= 0 ? String(row[idxVer] || "").trim() : "";

    if (sType === "PLAT_MONITOR_REVIEW" || key.startsWith("PLAT_REV:")) {
      const parts = key.split(":");
      const cycleKey = String(
        parsed.cycleKey || (parts.length >= 3 ? parts[1] : "")
      ).trim();
      const appNo = String(
        parsed.appNo || parsed.app || (parts.length >= 3 ? parts.slice(2).join(":") : "")
      ).trim();
      if (!appNo) continue;
      const revObj = {
        ...(parsed.review && typeof parsed.review === "object" ? parsed.review : parsed),
      };
      if (sheetYr && sheetYr !== "0" && !revObj.yearBuilt) revObj.yearBuilt = sheetYr;
      if (sheetDesig && !revObj.designation) revObj.designation = sheetDesig;
      if (sheetTier && !revObj.tier) revObj.tier = sheetTier;
      if (sheetReviewer && !revObj.reviewer) revObj.reviewer = sheetReviewer;
      const updatedMs = parseIsoMs(revObj.updated || parsed.updatedAt || "");
      platReviews.push({ cycleKey, appNo, review: revObj, updatedMs });
    } else if (sType === "PROPERTY_WATCHLIST_UPDATE" || key.startsWith("WATCH_PROP:")) {
      const propObj = {
        ...(parsed.property && typeof parsed.property === "object" ? parsed.property : parsed),
      };
      const pid = String(
        propObj.id || (key.startsWith("WATCH_PROP:") ? key.slice("WATCH_PROP:".length) : "")
      ).trim();
      if (!pid) continue;
      propObj.id = pid;
      if (sheetYr && sheetYr !== "0" && !propObj.yearBuilt) propObj.yearBuilt = sheetYr;
      if (sheetDesig && !propObj.designation) propObj.designation = sheetDesig;
      if (sheetTier && !propObj.tier) propObj.tier = sheetTier;
      if (sheetReviewer && !propObj.updatedBy) propObj.updatedBy = sheetReviewer;
      const updatedMs = parseIsoMs(propObj.updatedAt || parsed.updatedAt || "");
      const isRemovedTombstone =
        String(propObj.trackerStatus || "").trim() === REMOVED_TIER3_TOMBSTONE;
      watchProperties.push({
        id: pid,
        rawProp: propObj,
        isRemovedTombstone,
        updatedMs,
      });
    }
  }

  return { platReviews, watchProperties };
}

export function normalizeDondbHcadId(rawVal = "") {
  const raw = String(rawVal ?? "").trim();
  const base = raw.replace(/^#/, "").split("#")[0].trim();
  if (!base || base === "0") {
    return { hcad: "", raw, warning: "" };
  }
  if (/^[+-]?\d+(?:\.\d+)?E[+-]?\d+$/i.test(base)) {
    return {
      hcad: "",
      raw,
      warning: `Scientific notation '${raw}' corrupted 13-digit HCAD precision; re-export Parcel.ParcelID as text.`,
    };
  }
  const digits = base.replace(/[-\s]/g, "");
  if (/^\d+$/.test(digits)) {
    if (/^0+$/.test(digits)) return { hcad: "", raw, warning: "" };
    if (digits.length >= 10 && digits.length <= 13) {
      return { hcad: digits.padStart(13, "0"), raw, warning: "" };
    }
    if (digits.length > 13) {
      return {
        hcad: "",
        raw,
        warning: `Non-HCAD (${digits.length}-digit) parcel identifier '${raw}'.`,
      };
    }
    return {
      hcad: "",
      raw,
      warning: `Short (${digits.length}-digit) parcel identifier '${raw}'.`,
    };
  }
  return { hcad: "", raw, warning: `Unrecognized parcel ID format '${raw}'.` };
}

function pointInRing(lng, lat, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const xi = ring[i][0];
    const yi = ring[i][1];
    const xj = ring[j][0];
    const yj = ring[j][1];
    const intersect =
      yi > lat !== yj > lat && lng < ((xj - xi) * (lat - yi)) / (yj - yi || 1e-12) + xi;
    if (intersect) inside = !inside;
  }
  return inside;
}

function pointInPolygonRings(lng, lat, rings) {
  if (!Array.isArray(rings) || rings.length === 0) return false;
  if (!pointInRing(lng, lat, rings[0])) return false;
  for (let k = 1; k < rings.length; k += 1) {
    if (pointInRing(lng, lat, rings[k])) return false;
  }
  return true;
}

export function pointInGeoJsonGeometry(lng, lat, geometry) {
  if (!geometry || !Number.isFinite(lng) || !Number.isFinite(lat)) return false;
  if (geometry.type === "Polygon") {
    return pointInPolygonRings(lng, lat, geometry.coordinates);
  }
  if (geometry.type === "MultiPolygon") {
    return (geometry.coordinates || []).some((poly) => pointInPolygonRings(lng, lat, poly));
  }
  return false;
}

function collapseStreetWhitespace(str = "") {
  return String(str || "")
    .toUpperCase()
    .replace(/[.,#]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function parseHouseAndStreet(rawAddress = "") {
  const norm = collapseStreetWhitespace(rawAddress);
  const m = norm.match(/^(\d+[A-Z]?)\s+(?:1\/2\s+)?(.+)$/);
  if (!m) return null;
  const houseNum = m[1];
  const digits = houseNum.replace(/\D/g, "");
  if (!digits || /^0+$/.test(digits)) return null;
  const streetFull = m[2].trim();
  const streetSimplified = collapseStreetWhitespace(
    simplifySearchAddress(`${houseNum} ${streetFull}`)
  ).replace(/^\d+[A-Z]?\s+/, "");
  return {
    houseNum,
    digits,
    shardKey: digits.slice(0, 2).padEnd(2, "0"),
    streetFull,
    streetSimplified,
  };
}

/**
 * Parse and cross-check a City of Houston Dangerous Buildings CSV (DONDB*.csv) across:
 *   Stage 1: RFC-4180 CSV parsing + 13-digit leading-zero HCAD normalization & precision warnings
 *   Stage 2: Watched Properties + Curated Overrides + Pilot Core HCAD building/parcel maps
 *   Stage 3: Grouped countywide address_shards/addr_<prefix2>.json + optional HCAD ArcGIS fallback
 *   Stage 4: overlays.json point-in-polygon & landmark matching -> Tier 1 / Tier 2 / Non-Historic
 */
export async function parseAndMatchDangerousBuildingsCsv(csvText, ctx = {}) {
  const grid = parseCsvRfc4180(csvText);
  if (grid.length < 2) {
    return {
      totalRows: 0,
      tier1: [],
      tier2: [],
      nonHistoric: [],
      warnings: [],
    };
  }

  const rawHeaders = grid[0].map((h) => String(h || "").trim().toLowerCase());
  const findCol = (predicates) => {
    for (const pred of predicates) {
      const idx = rawHeaders.findIndex(pred);
      if (idx >= 0) return idx;
    }
    return -1;
  };

  const idxCase = findCol([(h) => h === "casenumber", (h) => h.includes("case")]);
  const idxParcel = findCol([
    (h) => h === "parcel.parcelid",
    (h) => h.includes("parcelid") || h.includes("hcad") || h.includes("account"),
  ]);
  const idxAddr = findCol([
    (h) => h === "parcel.parcelname",
    (h) => h.includes("parcelname") || h.includes("address") || h.includes("situs"),
  ]);
  const idxSec = findCol([(h) => h === "parcel.section", (h) => h.includes("section")]);
  const idxArea = findCol([(h) => h === "parcel.area.code", (h) => h.includes("area")]);
  const idxState = findCol([
    (h) => h === "processstate.code",
    (h) => h.includes("processstate") || h.includes("status") || h.includes("state"),
  ]);

  const watchedList = Array.isArray(ctx.watchedProperties) ? ctx.watchedProperties : [];
  const watchedByHcad = new Map();
  const watchedBySimpleAddr = new Map();
  for (const wp of watchedList) {
    for (const hid of parseHcadAccountList(wp.hcadAccount || "").hcadIds) {
      watchedByHcad.set(hid, wp);
    }
    for (const a of [wp.address, ...(wp.secondaryAddresses || [])]) {
      const sa = collapseStreetWhitespace(simplifySearchAddress(a || ""));
      if (sa) watchedBySimpleAddr.set(sa, wp);
    }
  }

  const overridesObj =
    ctx.curatedOverrides?.overrides || ctx.curatedOverrides || {};
  const localHcadMap = ctx.localHcadMap instanceof Map ? ctx.localHcadMap : new Map();

  // Index point-based landmarks, Good Brick Awards, NRHP properties, and THC markers from overlays
  const overlays = ctx.overlays || {};
  const pointDesignationsByHcad = new Map();
  const pointDesignationsByAddr = new Map();
  const addPointOverlay = (fc, labelFn) => {
    for (const feat of fc?.features || []) {
      const p = feat?.properties || {};
      const lbl = labelFn(p);
      if (!lbl) continue;
      const hid = normalizeDondbHcadId(p.hcad_num || p.hcad || "").hcad;
      if (hid) {
        if (!pointDesignationsByHcad.has(hid)) pointDesignationsByHcad.set(hid, []);
        pointDesignationsByHcad.get(hid).push({ label: lbl, props: p, coords: feat.geometry?.coordinates });
      }
      const sa = collapseStreetWhitespace(simplifySearchAddress(p.address || ""));
      if (sa) {
        if (!pointDesignationsByAddr.has(sa)) pointDesignationsByAddr.set(sa, []);
        pointDesignationsByAddr.get(sa).push({ label: lbl, props: p, coords: feat.geometry?.coordinates });
      }
    }
  };

  addPointOverlay(overlays.landmarks, (p) =>
    p.designation ? `${p.designation}${p.name ? ": " + p.name : ""}` : `City Landmark${p.name ? ": " + p.name : ""}`
  );
  addPointOverlay(overlays.good_brick_awards, (p) =>
    `Good Brick Award${p.name || p.landmark_name ? ": " + (p.name || p.landmark_name) : ""}`
  );
  addPointOverlay(overlays.nrhp_properties, (p) =>
    `NRHP Listed${p.name ? ": " + p.name : ""}`
  );
  addPointOverlay(overlays.thc_markers, (p) =>
    `THC Historic Marker${p.name ? ": " + p.name : ""}`
  );

  // Stage 1: Parse rows and group required address_shards
  const parsedRows = [];
  const warnings = [];
  const neededShards = new Set();

  for (let rIdx = 1; rIdx < grid.length; rIdx += 1) {
    const row = grid[rIdx];
    const caseNumber = idxCase >= 0 ? String(row[idxCase] || "").trim() : `ROW-${rIdx}`;
    const rawParcel = idxParcel >= 0 ? String(row[idxParcel] || "").trim() : "";
    const rawAddress = idxAddr >= 0 ? String(row[idxAddr] || "").trim() : "";
    const section = idxSec >= 0 ? String(row[idxSec] || "").trim() : "";
    const areaCode = idxArea >= 0 ? String(row[idxArea] || "").trim() : "";
    const processState = idxState >= 0 ? String(row[idxState] || "").trim() : "";

    const hcadNorm = normalizeDondbHcadId(rawParcel);
    if (hcadNorm.warning) {
      warnings.push({
        rowNumber: rIdx + 1,
        caseNumber,
        rawParcel,
        address: rawAddress,
        warning: hcadNorm.warning,
      });
    }

    const addrParts = parseHouseAndStreet(rawAddress);
    if (addrParts) {
      neededShards.add(addrParts.shardKey);
    }

    parsedRows.push({
      rowNumber: rIdx + 1,
      caseNumber,
      rawParcel,
      hcad: hcadNorm.hcad,
      hcadWarning: hcadNorm.warning,
      address: rawAddress,
      addrParts,
      section,
      areaCode,
      processState,
    });
  }

  // Stage 3a: Batch-load required countywide address_shards once per 2-digit prefix
  const shardIndexes = new Map();
  if (typeof ctx.loadAddressShard === "function") {
    await Promise.all(
      Array.from(neededShards).map(async (shardKey) => {
        try {
          const rawShard = (await ctx.loadAddressShard(shardKey)) || {};
          const byCollapsedStreet = new Map();
          const bySimplifiedStreet = new Map();
          const byHcadInShard = new Map();

          for (const [streetKey, entries] of Object.entries(rawShard)) {
            const normStreet = collapseStreetWhitespace(streetKey);
            const simpStreet = collapseStreetWhitespace(
              simplifySearchAddress(`100 ${normStreet}`)
            ).replace(/^100\s+/, "");

            if (!byCollapsedStreet.has(normStreet)) byCollapsedStreet.set(normStreet, []);
            if (simpStreet && !bySimplifiedStreet.has(simpStreet)) {
              bySimplifiedStreet.set(simpStreet, []);
            }

            for (const tuple of Array.isArray(entries) ? entries : []) {
              const rec = {
                houseNum: String(tuple[0] || "").toUpperCase(),
                lng: Number(tuple[1]),
                lat: Number(tuple[2]),
                yearBuilt: Number(tuple[3]) || 0,
                hcad: normalizeDondbHcadId(tuple[4] || "").hcad,
                street: normStreet,
              };
              byCollapsedStreet.get(normStreet).push(rec);
              if (simpStreet) bySimplifiedStreet.get(simpStreet).push(rec);
              if (rec.hcad) {
                if (!byHcadInShard.has(rec.hcad)) byHcadInShard.set(rec.hcad, []);
                byHcadInShard.get(rec.hcad).push(rec);
              }
            }
          }
          shardIndexes.set(shardKey, {
            byCollapsedStreet,
            bySimplifiedStreet,
            byHcadInShard,
          });
        } catch (_err) {
          shardIndexes.set(shardKey, {
            byCollapsedStreet: new Map(),
            bySimplifiedStreet: new Map(),
            byHcadInShard: new Map(),
          });
        }
      })
    );
  }

  // First pass enrichment across local maps + address_shards
  const unresolvedHcads = new Set();
  for (const item of parsedRows) {
    const validYears = [];
    const addYear = (y) => {
      const n = Number(y);
      if (Number.isFinite(n) && n >= 1836 && n <= 2035) validYears.push(Math.trunc(n));
    };

    let lng = null;
    let lat = null;
    let resolvedHcad = item.hcad;
    let resolvedAddr = item.address;
    let buildingName = "";
    const designations = [];

    const ov = resolvedHcad && overridesObj[resolvedHcad] ? overridesObj[resolvedHcad] : null;
    if (ov) {
      addYear(ov.year_built);
      if (ov.landmark_name) {
        buildingName = ov.landmark_name;
        designations.push(`City Landmark: ${ov.landmark_name}`);
      }
      if (ov.historic_district) {
        designations.push(`City Historic District: ${ov.historic_district}`);
      }
      if (ov.good_brick_summary) {
        designations.push("Good Brick Award");
      }
      if (ov.address && (!resolvedAddr || /^0\s+/i.test(resolvedAddr))) {
        resolvedAddr = ov.address;
      }
    }

    const loc = resolvedHcad ? localHcadMap.get(resolvedHcad) : null;
    if (loc) {
      for (const y of loc.allYears || [loc.yearBuilt]) addYear(y);
      if (Number.isFinite(loc.lng) && Number.isFinite(loc.lat)) {
        lng = loc.lng;
        lat = loc.lat;
      }
      if (loc.buildingName && !buildingName) buildingName = loc.buildingName;
      if (loc.historicDistrict) {
        designations.push(`City Historic District: ${loc.historicDistrict}`);
      }
      if (loc.landmarkName) {
        designations.push(`City Landmark: ${loc.landmarkName}`);
      }
      if (loc.address && (!resolvedAddr || /^0\s+/i.test(resolvedAddr))) {
        resolvedAddr = loc.address;
      }
    }

    if (item.addrParts && shardIndexes.has(item.addrParts.shardKey)) {
      const sIdx = shardIndexes.get(item.addrParts.shardKey);
      let candidates = [];
      if (resolvedHcad && sIdx.byHcadInShard.has(resolvedHcad)) {
        candidates = sIdx.byHcadInShard.get(resolvedHcad);
      } else {
        const exactStreet = sIdx.byCollapsedStreet.get(item.addrParts.streetFull) || [];
        candidates = exactStreet.filter((c) => c.houseNum === item.addrParts.houseNum);
        if (candidates.length === 0 && item.addrParts.streetSimplified) {
          const simpMatches =
            sIdx.bySimplifiedStreet.get(item.addrParts.streetSimplified) || [];
          candidates = simpMatches.filter((c) => c.houseNum === item.addrParts.houseNum);
        }
      }
      for (const c of candidates) {
        addYear(c.yearBuilt);
        if (!resolvedHcad && c.hcad) resolvedHcad = c.hcad;
        if (lng === null && Number.isFinite(c.lng) && Number.isFinite(c.lat)) {
          lng = c.lng;
          lat = c.lat;
        }
      }
    }

    if (resolvedHcad && validYears.length === 0 && lng === null) {
      unresolvedHcads.add(resolvedHcad);
    }

    item._Work = {
      resolvedHcad,
      resolvedAddr,
      buildingName,
      validYears,
      lng,
      lat,
      designations,
    };
  }

  // Stage 3b: Optional batched HCAD fallback for HCAD-only misses (e.g. "0 HARRIS ST")
  if (unresolvedHcads.size > 0 && typeof ctx.lookupHcadAccounts === "function") {
    try {
      const fallbackMap = (await ctx.lookupHcadAccounts(Array.from(unresolvedHcads))) || {};
      for (const item of parsedRows) {
        const w = item._Work;
        const fb = w.resolvedHcad ? fallbackMap[w.resolvedHcad] : null;
        if (!fb) continue;
        const n = Number(fb.yearBuilt);
        if (Number.isFinite(n) && n >= 1836 && n <= 2035) w.validYears.push(Math.trunc(n));
        if (w.lng === null && Number.isFinite(fb.lng) && Number.isFinite(fb.lat)) {
          w.lng = fb.lng;
          w.lat = fb.lat;
        }
        if (fb.address && (!w.resolvedAddr || /^0\s+/i.test(w.resolvedAddr))) {
          w.resolvedAddr = fb.address;
        }
      }
    } catch (_e) {
      // Graceful degradation when offline
    }
  }

  // Stage 4: Spatial overlay checks & priority tier classification
  const tier1 = [];
  const tier2 = [];
  const nonHistoric = [];

  const checkPolygonOverlay = (lng, lat, fc, prefix) => {
    if (!Number.isFinite(lng) || !Number.isFinite(lat) || !fc?.features) return [];
    const hits = [];
    for (const feat of fc.features) {
      if (pointInGeoJsonGeometry(lng, lat, feat.geometry)) {
        const nm = feat.properties?.name || feat.properties?.raw_name || "Unnamed";
        hits.push(`${prefix}: ${nm}`);
      }
    }
    return hits;
  };

  for (const item of parsedRows) {
    const w = item._Work;
    const simpAddr = collapseStreetWhitespace(simplifySearchAddress(w.resolvedAddr || ""));

    const watchedHit =
      (w.resolvedHcad && watchedByHcad.get(w.resolvedHcad)) ||
      (simpAddr && watchedBySimpleAddr.get(simpAddr)) ||
      null;

    const ptHits = [
      ...(w.resolvedHcad ? pointDesignationsByHcad.get(w.resolvedHcad) || [] : []),
      ...(simpAddr ? pointDesignationsByAddr.get(simpAddr) || [] : []),
    ];
    for (const ph of ptHits) {
      w.designations.push(ph.label);
      if (!w.buildingName && (ph.props?.name || ph.props?.landmark_name)) {
        w.buildingName = ph.props.name || ph.props.landmark_name;
      }
      if (ph.props?.year_built) {
        const py = Number(ph.props.year_built);
        if (Number.isFinite(py) && py >= 1836 && py <= 2035) w.validYears.push(Math.trunc(py));
      }
      if (w.lng === null && Array.isArray(ph.coords) && ph.coords.length >= 2) {
        w.lng = Number(ph.coords[0]);
        w.lat = Number(ph.coords[1]);
      }
    }

    const hdHits = checkPolygonOverlay(
      w.lng,
      w.lat,
      overlays.historic_districts,
      "City Historic District"
    );
    const herHits = checkPolygonOverlay(
      w.lng,
      w.lat,
      overlays.heritage_districts,
      "Heritage District"
    );
    const nrhpDistHits = checkPolygonOverlay(
      w.lng,
      w.lat,
      overlays.nrhp_districts,
      "NRHP Historic District"
    );
    const potDistHits = checkPolygonOverlay(
      w.lng,
      w.lat,
      overlays.potential_historic_districts,
      "Potential Historic District"
    );

    const hardDesignations = uniqueNonEmptyStrings([
      ...w.designations,
      ...hdHits,
      ...herHits,
      ...nrhpDistHits,
    ]);
    const potentialDesignations = uniqueNonEmptyStrings(potDistHits);

    const sortedYears = Array.from(new Set(w.validYears)).sort((a, b) => a - b);
    const yearBuilt = sortedYears.length > 0 ? sortedYears[0] : 0;

    let bucket = "nonHistoric";
    const reasons = [];
    if (watchedHit) {
      bucket = "tier1";
      reasons.push(`Active Watchlist Property (${watchedHit.propertyName})`);
    }
    if (hardDesignations.length > 0) {
      bucket = "tier1";
      reasons.push(...hardDesignations);
    }
    if (yearBuilt >= 1836 && yearBuilt <= 1940) {
      bucket = "tier1";
      reasons.push(`Pre-1940 structure (Built ${yearBuilt})`);
    }

    if (bucket !== "tier1") {
      if (potentialDesignations.length > 0) {
        bucket = "tier2";
        reasons.push(...potentialDesignations);
      }
      if (yearBuilt >= 1941 && yearBuilt <= 1976) {
        bucket = "tier2";
        reasons.push(`1941-1976 historic-age structure (Built ${yearBuilt})`);
      }
    } else if (potentialDesignations.length > 0) {
      reasons.push(...potentialDesignations);
    }

    const record = {
      rowNumber: item.rowNumber,
      caseNumber: item.caseNumber,
      hcad: w.resolvedHcad,
      rawParcel: item.rawParcel,
      hcadWarning: item.hcadWarning,
      address: w.resolvedAddr || item.address,
      buildingName: w.buildingName || watchedHit?.propertyName || "",
      yearBuilt,
      allYearsBuilt: sortedYears,
      section: item.section,
      areaCode: item.areaCode,
      processState: item.processState,
      watchedPropertyId: watchedHit?.id || "",
      designations: uniqueNonEmptyStrings([...hardDesignations, ...potentialDesignations]),
      reasons: uniqueNonEmptyStrings(reasons),
      lng: w.lng,
      lat: w.lat,
      tier: bucket === "tier1" ? "1" : bucket === "tier2" ? "2" : "",
    };

    if (bucket === "tier1") tier1.push(record);
    else if (bucket === "tier2") tier2.push(record);
    else nonHistoric.push(record);
  }

  const byAgeAsc = (a, b) =>
    (a.yearBuilt || 9999) - (b.yearBuilt || 9999) ||
    String(a.address).localeCompare(String(b.address));
  tier1.sort(byAgeAsc);
  tier2.sort(byAgeAsc);

  return {
    totalRows: parsedRows.length,
    tier1,
    tier2,
    nonHistoric,
    warnings,
  };
}

export function buildDangerousBuildingsNotificationText(matches = []) {
  const items = Array.isArray(matches) ? matches : [];
  const lines = [
    "City of Houston Department of Neighborhoods - Dangerous Buildings Team:",
    "",
    "Preservation Houston cross-checked the latest Dangerous Buildings (DONDB) case list against City Landmarks, Historic Districts, National Register listings, and pre-1976 historic structures. Please flag the following properties for historic-preservation review prior to any demolition order:",
    "",
  ];
  for (const m of items) {
    const parts = [
      `- Case ${m.caseNumber || "N/A"}: ${m.address || "Address N/A"}`,
      m.buildingName ? `(${m.buildingName})` : "",
      m.hcad ? `| HCAD ${m.hcad}` : "",
      m.yearBuilt ? `| Built ${m.yearBuilt}` : "",
      m.processState ? `| State: ${m.processState}` : "",
      m.reasons?.length ? `| Significance: ${m.reasons.join("; ")}` : "",
    ].filter(Boolean);
    lines.push(parts.join(" "));
  }
  lines.push("", "Thank you,", "Preservation Houston");
  return lines.join("\n");
}
