/**
 * Canonical Parcel-Centered Preservation Monitoring Model (JavaScript ES Module).
 *
 * Pure, zero-DOM, zero-network domain model backed by `MONITORING_SCHEMA_CONTRACT` and kept
 * in 1:1 behavioral parity with `pipeline/atlas_pipeline/parcel_monitoring_model.py`.
 *
 * Why separate Significance, Protection, Threat Stage, Advocacy Status, and Outcome:
 *   Legacy Tiers 1..4 conflated architectural/cultural importance with immediacy of loss and
 *   case disposition, forcing reviewers to re-tier repeated plat filings and mixing historic
 *   demolitions ("lost") with vacant/non-historic parcels ("not_relevant"). Recording
 *   Significance once per parcel allows Attention Level ("act" | "watch" | "record") to
 *   recalculate automatically as filings arrive.
 */

import { MONITORING_SCHEMA_CONTRACT } from "./monitoringSchemaContract.js?v=20261113c";

export { MONITORING_SCHEMA_CONTRACT };

const TERMINAL_OUTCOMES_SET = new Set(MONITORING_SCHEMA_CONTRACT.enums.terminalOutcomes);
const VALID_ATTENTION_SET = new Set(MONITORING_SCHEMA_CONTRACT.enums.attentionLevels);
const VALID_SIGNIFICANCE_SET = new Set(MONITORING_SCHEMA_CONTRACT.enums.significanceLevels);
const VALID_REVIEW_STATUS_SET = new Set(MONITORING_SCHEMA_CONTRACT.enums.reviewStatuses);
const VALID_BASIS_SET = new Set(MONITORING_SCHEMA_CONTRACT.enums.significanceBases);
const VALID_CONTRIBUTING_SET = new Set(MONITORING_SCHEMA_CONTRACT.enums.contributingStatuses);
const VALID_INTEGRITY_SET = new Set(MONITORING_SCHEMA_CONTRACT.enums.integrityGrades);
const VALID_PROTECTION_SET = new Set(MONITORING_SCHEMA_CONTRACT.enums.protectionLevels);
const VALID_ADVOCACY_SET = new Set(MONITORING_SCHEMA_CONTRACT.enums.advocacyStatuses);
const VALID_OUTCOME_SET = new Set(MONITORING_SCHEMA_CONTRACT.enums.outcomes);

const PIPELINE_MACHINE_PARCEL_COLS = new Set(
  MONITORING_SCHEMA_CONTRACT.columnOwnership.pipelineMachineOwnedParcelCols
);
const SCREENER_DECISION_COLS = new Set(
  MONITORING_SCHEMA_CONTRACT.columnOwnership.screenerWritableDecisionCols
);
const STAFF_ONLY_DECISION_COLS = new Set(
  MONITORING_SCHEMA_CONTRACT.columnOwnership.staffOnlyDecisionCols
);

const CORP_NOISE_TOKENS_RE =
  /\b(?:LLC|L\s*L\s*C|INC|INCORPORATED|CORP|CORPORATION|LTD|LIMITED|LP|L\s*P|LLP|CO|COMPANY|PROPERTIES|INVESTMENTS|HOLDINGS|GROUP|TRUST|ESTATE\s+OF|ET\s+AL)\b/gi;

const CHICAGO_DATE_FMT = new Intl.DateTimeFormat("en-CA", {
  timeZone: "America/Chicago",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

export function sanitizeMonitoringText(rawVal) {
  if (rawVal === null || rawVal === undefined) return "";
  return String(rawVal)
    .replace(/ \u2014 /g, " - ")
    .replace(/\u2014/g, "-")
    .replace(/ \u2013 /g, " - ")
    .replace(/\u2013/g, "-")
    .trim();
}

/**
 * Strip embedded `[GEOMETRY_GEOJSON: {...}]` spans from citation text using balanced
 * brace/string scanning so nested GeoJSON arrays or `GeometryCollection` objects never
 * leave trailing JSON fragments in `source_citation`.
 */
export function stripEmbeddedGeometryTag(rawCitation = "") {
  let s = String(rawCitation || "");
  const prefix = "[GEOMETRY_GEOJSON:";
  let searchFrom = 0;
  for (;;) {
    const idx = s.toUpperCase().indexOf(prefix, searchFrom);
    if (idx === -1) break;
    let i = idx + prefix.length;
    while (i < s.length && /\s/.test(s[i])) i += 1;
    if (s[i] !== "{") {
      searchFrom = idx + prefix.length;
      continue;
    }
    const startJson = i;
    let depth = 0;
    let inStr = false;
    let esc = false;
    let endJson = -1;
    let endIdx = -1;
    for (; i < s.length; i += 1) {
      const ch = s[i];
      if (inStr) {
        if (esc) esc = false;
        else if (ch === "\\") esc = true;
        else if (ch === '"') inStr = false;
      } else if (ch === '"') {
        inStr = true;
      } else if (ch === "{") {
        depth += 1;
      } else if (ch === "}") {
        depth -= 1;
        if (depth === 0) {
          endJson = i + 1;
          let close = endJson;
          while (close < s.length && /\s/.test(s[close])) close += 1;
          if (s[close] === "]") endIdx = close + 1;
          break;
        }
      }
    }
    if (endIdx === -1 || endJson === -1) {
      searchFrom = idx + prefix.length;
      continue;
    }
    try {
      JSON.parse(s.slice(startJson, endJson));
    } catch (_e) {
      searchFrom = idx + prefix.length;
      continue;
    }
    s = `${s.slice(0, idx)}${s.slice(endIdx)}`;
    searchFrom = idx;
  }
  return sanitizeMonitoringText(s)
    .replace(/[ \t]{2,}/g, " ")
    .replace(/\s*\|\s*$/, "")
    .replace(/^\s*\|\s*/, "")
    .replace(/\s*\|\s*\|\s*/g, " | ")
    .trim();
}

/**
 * Normalize an ISO timestamp or date string into a `YYYY-MM-DD` calendar date in `America/Chicago`
 * so 14-day hearing windows and 180-day historical baseline checks evaluate identically on browsers
 * in any timezone and UTC GitHub Actions runners.
 */
export function toChicagoCalendarDate(rawTsOrDate) {
  const s = sanitizeMonitoringText(rawTsOrDate);
  if (!s) return "";
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return s;
  const slashMatch = s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
  if (slashMatch) {
    const mm = String(Number(slashMatch[1])).padStart(2, "0");
    const dd = String(Number(slashMatch[2])).padStart(2, "0");
    return `${slashMatch[3]}-${mm}-${dd}`;
  }
  const ms = Date.parse(s);
  if (!Number.isFinite(ms)) {
    const prefixMatch = s.match(/^(\d{4}-\d{2}-\d{2})/);
    return prefixMatch ? prefixMatch[1] : "";
  }
  const parts = CHICAGO_DATE_FMT.formatToParts(new Date(ms));
  const y = parts.find((p) => p.type === "year")?.value || "";
  const m = parts.find((p) => p.type === "month")?.value || "";
  const d = parts.find((p) => p.type === "day")?.value || "";
  return y && m && d ? `${y}-${m}-${d}` : "";
}

/**
 * Pure calendar-day delta (`endYmd - startYmd`) immune to DST hour shifts.
 */
export function calendarDaysBetween(startRaw, endRaw) {
  const startYmd = toChicagoCalendarDate(startRaw);
  const endYmd = toChicagoCalendarDate(endRaw);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(startYmd) || !/^\d{4}-\d{2}-\d{2}$/.test(endYmd)) {
    return null;
  }
  const [sy, sm, sd] = startYmd.split("-").map(Number);
  const [ey, em, ed] = endYmd.split("-").map(Number);
  const startUtc = Date.UTC(sy, sm - 1, sd);
  const endUtc = Date.UTC(ey, em - 1, ed);
  return Math.round((endUtc - startUtc) / 86400000);
}

export function normalizeHcad13(rawVal) {
  const base = sanitizeMonitoringText(rawVal).split("#")[0].replace(/[\s-]+/g, "");
  if (!/^\d{10,13}$/.test(base)) return "";
  return base.padStart(13, "0");
}

export function parseDelimitedList(rawVal) {
  if (Array.isArray(rawVal)) {
    return Array.from(
      new Set(rawVal.map((v) => sanitizeMonitoringText(v)).filter(Boolean))
    );
  }
  const s = sanitizeMonitoringText(rawVal);
  if (!s) return [];
  return Array.from(
    new Set(
      s
        .split(/[;|,]+/)
        .map((part) => part.trim())
        .filter(Boolean)
    )
  );
}

export function parseRelatedHcads(rawVal) {
  const tokens = Array.isArray(rawVal)
    ? rawVal
    : sanitizeMonitoringText(rawVal).split(/[;|,\s]+/);
  const out = [];
  for (const tok of tokens) {
    const norm = normalizeHcad13(tok);
    if (norm && !out.includes(norm)) out.push(norm);
  }
  return out;
}

function slugifyToken(rawVal, fallback = "UNKNOWN") {
  const slug = sanitizeMonitoringText(rawVal)
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 64);
  return slug || fallback;
}

/**
 * Mint a deterministic synthetic `parcel_id` for ETJ tracts, street dedications, or unplatted
 * reserves lacking an HCAD account so zero-HCAD Plat Reviews never collide or drop during migration.
 */
export function mintSyntheticUnparceledId({ appNo = "", address = "", name = "" } = {}) {
  const cleanApp = sanitizeMonitoringText(appNo);
  if (cleanApp) {
    return `UNPARCELED:APP_${slugifyToken(cleanApp)}`;
  }
  const cleanAddr = sanitizeMonitoringText(address);
  if (cleanAddr) {
    return `UNPARCELED:ADDR_${slugifyToken(cleanAddr)}`;
  }
  return `UNPARCELED:NAME_${slugifyToken(name, "ITEM")}`;
}

/**
 * Build a secondary lookup index mapping any member 13-digit `hcad_num` (and normalized perimeter
 * address) to its parent `CAMPUS:<slug>` or canonical `parcel_id` so multi-parcel church, school,
 * and commercial assemblage filings roll up onto one record instead of spawning orphan rows.
 */
export function buildCampusParcelIndex(parcels = []) {
  const byParcelId = new Map();
  const byMemberHcad = new Map();
  const byNormalizedAddress = new Map();

  const normAddrKey = (addr) =>
    sanitizeMonitoringText(addr)
      .toUpperCase()
      .replace(/[.,#]/g, " ")
      .replace(/\s+/g, " ")
      .trim();

  const list = Array.isArray(parcels)
    ? parcels
    : parcels && typeof parcels === "object"
    ? Object.values(parcels)
    : [];

  // Index explicit CAMPUS:<slug> records first so multi-parcel assemblages win over single-member rows.
  const sorted = list.slice().sort((a, b) => {
    const aCampus = String(a?.parcel_id || a?.campus_group_id || "").startsWith("CAMPUS:") ? 0 : 1;
    const bCampus = String(b?.parcel_id || b?.campus_group_id || "").startsWith("CAMPUS:") ? 0 : 1;
    if (aCampus !== bCampus) return aCampus - bCampus;
    return String(a?.parcel_id || "").localeCompare(String(b?.parcel_id || ""));
  });

  for (const p of sorted) {
    if (!p || typeof p !== "object") continue;
    const pid = sanitizeMonitoringText(p.parcel_id || p.campus_group_id || normalizeHcad13(p.hcad_num));
    if (!pid) continue;
    byParcelId.set(pid, p);
    const hcads = parseRelatedHcads([
      p.hcad_num || "",
      ...(Array.isArray(p.related_hcads) ? p.related_hcads : parseDelimitedList(p.related_hcads)),
    ]);
    for (const hid of hcads) {
      if (!byMemberHcad.has(hid)) {
        byMemberHcad.set(hid, pid);
      }
    }
    const addrs = [
      p.primary_address || "",
      ...parseDelimitedList(p.perimeter_addresses),
    ];
    for (const rawA of addrs) {
      const ak = normAddrKey(rawA);
      if (ak && !byNormalizedAddress.has(ak)) {
        byNormalizedAddress.set(ak, pid);
      }
    }
  }

  return { byParcelId, byMemberHcad, byNormalizedAddress };
}

export function resolveCanonicalParcelId(
  { parcelId = "", campusGroupId = "", hcadIds = [], appNo = "", address = "", name = "" } = {},
  campusIndex = null
) {
  const explicitCampus = sanitizeMonitoringText(campusGroupId);
  if (explicitCampus) {
    return explicitCampus.startsWith("CAMPUS:")
      ? explicitCampus
      : `CAMPUS:${slugifyToken(explicitCampus)}`;
  }
  const explicitPid = sanitizeMonitoringText(parcelId);
  if (explicitPid && (explicitPid.startsWith("CAMPUS:") || explicitPid.startsWith("UNPARCELED:"))) {
    return explicitPid;
  }
  const normHcads = parseRelatedHcads(
    explicitPid ? [explicitPid, ...(Array.isArray(hcadIds) ? hcadIds : [hcadIds])] : hcadIds
  );
  if (campusIndex && campusIndex.byMemberHcad) {
    for (const hid of normHcads) {
      const mapped = campusIndex.byMemberHcad.get(hid);
      if (mapped) return mapped;
    }
  }
  if (normHcads.length > 0) {
    return normHcads[0];
  }
  if (campusIndex && campusIndex.byNormalizedAddress && address) {
    const ak = sanitizeMonitoringText(address)
      .toUpperCase()
      .replace(/[.,#]/g, " ")
      .replace(/\s+/g, " ")
      .trim();
    const mappedByAddr = campusIndex.byNormalizedAddress.get(ak);
    if (mappedByAddr) return mappedByAddr;
  }
  return mintSyntheticUnparceledId({ appNo, address, name });
}

function lookupAreaRecord(areasById, areaId) {
  if (!areasById || !areaId) return null;
  if (areasById instanceof Map) return areasById.get(areaId) || null;
  if (Array.isArray(areasById)) {
    return areasById.find((a) => a && a.area_id === areaId) || null;
  }
  if (typeof areasById === "object") return areasById[areaId] || null;
  return null;
}

/**
 * Aggregate multi-area rules across `parcel.area_ids`: highest `priority_status` rank
 * (`priority > candidate > standard`) and the most inclusive (`Math.max`) `age_rule_cutoff_year`.
 */
export function resolveEffectiveAreaContext(
  areaIdsRaw,
  areasById = null,
  defaultCutoffYear = MONITORING_SCHEMA_CONTRACT.defaultAgeCutoffYear
) {
  const areaIds = parseDelimitedList(areaIdsRaw);
  const ranks = MONITORING_SCHEMA_CONTRACT.enums.priorityStatusRanks;
  let bestStatus = "outside";
  let bestRank = 0;
  let maxCutoff = Number(defaultCutoffYear) || MONITORING_SCHEMA_CONTRACT.defaultAgeCutoffYear;
  const matchedAreas = [];

  for (const aid of areaIds) {
    const rec = lookupAreaRecord(areasById, aid);
    if (!rec) {
      // Infer priority/candidate prefix if area metadata map was not yet hydrated
      if (aid.startsWith("area:priority:") && bestRank < ranks.priority) {
        bestStatus = "priority";
        bestRank = ranks.priority;
      } else if (aid.startsWith("area:candidate:") && bestRank < ranks.candidate) {
        bestStatus = "candidate";
        bestRank = ranks.candidate;
      } else if (bestRank < ranks.standard) {
        bestStatus = "standard";
        bestRank = ranks.standard;
      }
      continue;
    }
    matchedAreas.push(rec);
    const st = String(rec.priority_status || "standard").trim().toLowerCase();
    const r = ranks[st] || ranks.standard;
    if (r > bestRank) {
      bestRank = r;
      bestStatus = st;
    }
    const cutoff = Number(rec.age_rule_cutoff_year) || 0;
    const devEnd = Number(rec.dev_period_end) || 0;
    const effAreaCutoff = Math.max(cutoff, devEnd);
    if (effAreaCutoff > maxCutoff) {
      maxCutoff = effAreaCutoff;
    }
  }

  return {
    areaIds,
    matchedAreas,
    inTrackedArea: bestRank > 0,
    priorityStatus: bestStatus,
    ageRuleCutoffYear: maxCutoff,
  };
}

/**
 * Classify municipal/statutory protection under COH Code of Ordinances Ch. 33:
 * - "binding": Protected Landmarks (PLM) and City of Houston Historic Districts require binding
 *   HAHC Certificate of Appropriateness review without a 90-day demolition waiver.
 * - "limited": Regular City Landmarks (90-day waiver), National Register (NRHP), RTHL, and SAL.
 * - "none": Unprotected parcels (including honorary Good Brick or archival 1915 Red Book sites).
 */
export function classifyProtectionLevel(
  designationSummary = "",
  {
    inHistoricDistrict = false,
    isProtectedLandmark = false,
    isCityLandmark = false,
    isNrhp = false,
  } = {}
) {
  const s = sanitizeMonitoringText(designationSummary).toLowerCase();
  if (
    isProtectedLandmark ||
    inHistoricDistrict ||
    /\bprotected\s+landmark\b|\bplm\b|\bcity\s+historic\s+district\b|\bcoh\s+historic\s+district\b/.test(s) ||
    (/\bhistoric\s+district\b/.test(s) && !/\bnational\s+register\b|\bnrhp\b|\bpotential\b/.test(s))
  ) {
    return "binding";
  }
  if (
    isCityLandmark ||
    isNrhp ||
    /\bcity\s+landmark\b|\bnational\s+register\b|\bnrhp\b|\brecorded\s+texas\s+historic\s+landmark\b|\brthl\b|\bstate\s+antiquities\b|\bsal\b/.test(
      s
    )
  ) {
    return "limited";
  }
  return "none";
}

export function computeContributingStatus(parcel = {}, areaContext = null) {
  const ov = sanitizeMonitoringText(parcel.contributing_override).toLowerCase();
  if (ov && VALID_CONTRIBUTING_SET.has(ov)) {
    return ov;
  }
  const outcome = sanitizeMonitoringText(parcel.outcome).toLowerCase();
  if (outcome === "lost" || outcome === "lost_before_review" || outcome === "not_relevant") {
    return "non_contributing";
  }
  const ctx =
    areaContext ||
    resolveEffectiveAreaContext(parcel.area_ids, null, MONITORING_SCHEMA_CONTRACT.defaultAgeCutoffYear);
  const desig = sanitizeMonitoringText(parcel.designation_summary).toLowerCase();
  const inDistrict = /\bhistoric\s+district\b/.test(desig);
  if (!ctx.inTrackedArea && !inDistrict) {
    return "outside_area";
  }
  const yr = Number(parcel.year_built) || 0;
  if (
    yr >= MONITORING_SCHEMA_CONTRACT.minStandingYear &&
    yr <= ctx.ageRuleCutoffYear
  ) {
    return "contributing";
  }
  return "non_contributing";
}

export function normalizePartyComparisonKey(rawName) {
  const cleaned = sanitizeMonitoringText(rawName)
    .toUpperCase()
    .replace(/[.,'"()/-]+/g, " ")
    .replace(CORP_NOISE_TOKENS_RE, " ")
    .replace(/\s+/g, " ")
    .trim();
  return cleaned;
}

/**
 * Detect non-obvious early warning signals from cross-source party and land-use mismatches:
 * 1. `owner_applicant_mismatch`: Replat/TDLR/TABC filing names an applicant/tenant/developer
 *    distinct from HCAD `owner_of_record`, signaling a pending sale or ground lease before deed recording.
 * 2. `commercial_on_residential`: Commercial filings (TABC, TDLR commercial/new construction,
 *    unrestricted reserve replat) targeting an HCAD single-family/residential (`A*`/`B*`) parcel.
 */
export function detectParcelSignalFlags({
  ownerOfRecord = "",
  partyNames = [],
  landUseClass = "",
  eventKinds = [],
} = {}) {
  const flags = [];
  const ownerKey = normalizePartyComparisonKey(ownerOfRecord);
  const ownerTokens = new Set(ownerKey.split(" ").filter((t) => t.length >= 3));
  const parties = parseDelimitedList(partyNames);

  if (ownerTokens.size > 0 && parties.length > 0) {
    let hasDisjointNamedParty = false;
    for (const p of parties) {
      const pk = normalizePartyComparisonKey(p);
      if (!pk || pk.length < 3) continue;
      // Ignore surveying/platting consultants when checking buyer/tenant mismatch if marked as surveyor
      if (/\b(?:SURVEY|SURVEYING|ENGINEERING|ENGINEERS|PLATTING)\b/.test(p.toUpperCase())) {
        continue;
      }
      const pTokens = pk.split(" ").filter((t) => t.length >= 3);
      if (pTokens.length === 0) continue;
      const sharesToken = pTokens.some((tok) => ownerTokens.has(tok));
      if (!sharesToken) {
        hasDisjointNamedParty = true;
        break;
      }
    }
    if (hasDisjointNamedParty) {
      flags.push("owner_applicant_mismatch");
    }
  }

  const luUpper = sanitizeMonitoringText(landUseClass).toUpperCase();
  const isResidentialClass =
    /^(?:A\d|B\d|RESIDENTIAL|SINGLE[-\s]*FAMILY)/.test(luUpper);
  if (isResidentialClass) {
    const kindsJoined = parseDelimitedList(eventKinds).join(" ").toLowerCase();
    if (
      /\b(?:tabc|liquor|tdlr|commercial|unrestricted_reserve|change_of_use|comptroller|tceq)\b/.test(
        kindsJoined
      )
    ) {
      flags.push("commercial_on_residential");
    }
  }

  return flags;
}

/**
 * Return true when an agency record predates parcel onboarding (`filingDate < parcelAddedAt`)
 * or exceeds the 180-day baseline cutoff in `America/Chicago` calendar days so historical
 * filings populate parcel timelines without firing false net-new critical alerts.
 */
export function evaluateHistoricalBaselineEvent({
  filingDate = "",
  parcelAddedAt = "",
  asOfDateCst = "",
  maxAgeDays = MONITORING_SCHEMA_CONTRACT.historicalBaselineMaxAgeDays,
} = {}) {
  const filingYmd = toChicagoCalendarDate(filingDate);
  if (!filingYmd) return false;
  const addedYmd = toChicagoCalendarDate(parcelAddedAt);
  if (addedYmd && filingYmd < addedYmd) {
    return true;
  }
  const refYmd = toChicagoCalendarDate(asOfDateCst);
  if (refYmd) {
    const ageDays = calendarDaysBetween(filingYmd, refYmd);
    if (ageDays !== null && ageDays > maxAgeDays) {
      return true;
    }
  }
  return false;
}

export function mapAgencyOrPermitToThreatStage({
  sourceAgency = "",
  eventKind = "",
  summaryText = "",
  adverse = true,
} = {}) {
  if (adverse === false) return 0;
  const agencyKey = sanitizeMonitoringText(sourceAgency).toLowerCase();
  const kindLower = sanitizeMonitoringText(eventKind).toLowerCase();
  const textLower = `${kindLower} ${sanitizeMonitoringText(summaryText).toLowerCase()}`;

  if (
    /\b(?:building\s+demolition|type\s+sd\b|dangerous\s+building\s+demolition|type\s+4d\b|active\s+teardown|demolished\s+on\s+site|stage\s*5)\b/.test(
      textLower
    )
  ) {
    return 5;
  }
  if (
    /\b(?:sewer\s+disconnect|12\/4p|asbestos|dshs|dangerous\s+building|dondb|311\s+dangerous|stage\s*4)\b/.test(
      textLower
    )
  ) {
    return 4;
  }
  if (
    /\b(?:replat|plat|variance|dpv|tabc|liquor|tdlr|tabs|plan\s+review|ilms|abandonment|tceq|comptroller|stage\s*3)\b/.test(
      textLower
    )
  ) {
    return 3;
  }
  if (/\b(?:deed|ownership\s+change|recent\s+sale|new\s+llc|sos|stage\s*2)\b/.test(textLower)) {
    return 2;
  }
  if (/\b(?:for\s+sale|loopnet|listing|broker\s+sign|surplus|escrow|stage\s*1)\b/.test(textLower)) {
    return 1;
  }
  const mapped = MONITORING_SCHEMA_CONTRACT.agencyThreatStageMap[agencyKey];
  return Number.isInteger(mapped) ? mapped : 0;
}

export function computeThreatStageFromEvents(events = [], parcel = {}) {
  const adv = sanitizeMonitoringText(parcel.advocacy_status).toLowerCase();
  const out = sanitizeMonitoringText(parcel.outcome).toLowerCase();
  if (adv === "closed" || TERMINAL_OUTCOMES_SET.has(out)) {
    return 6;
  }
  let maxStage = 0;
  for (const ev of Array.isArray(events) ? events : []) {
    if (!ev || typeof ev !== "object") continue;
    if (ev.is_historical_baseline === true || ev.is_historical_baseline === "true") {
      continue;
    }
    const st = Number(ev.threat_stage) || 0;
    if (st >= 1 && st <= 5 && st > maxStage) {
      maxStage = st;
    }
  }
  return maxStage;
}

/**
 * Deterministic Attention Level (`"act"` | `"watch"` | `"record"`) implementing Section 2.B:
 * - Stage `6` (`Outcome`) is strictly non-active (`1 <= threat_stage <= 5` bounds active threats).
 * - Binding COH Ch. 33 HAHC review (`protection_level === "binding"`) downgrades Stage `1..3`
 *   threats one notch from `"act"` to `"watch"`, while Stage `4..5` emergency bypasses stay `"act"`.
 * - Priority-area contributing buildings facing Stage `3..5` replats/splits elevate to `"watch"`
 *   unless closed or explicitly placed in `"recording_only"`.
 */
export function computeAttentionLevel(
  parcel = {},
  { asOfDateCst = "", nextHearingDateCst = "", areasById = null } = {}
) {
  const override = sanitizeMonitoringText(parcel.attention_override).toLowerCase();
  if (override && VALID_ATTENTION_SET.has(override)) {
    return override;
  }

  const advocacyStatus = sanitizeMonitoringText(parcel.advocacy_status).toLowerCase();
  const outcome = sanitizeMonitoringText(parcel.outcome).toLowerCase();

  if (advocacyStatus === "closed" || TERMINAL_OUTCOMES_SET.has(outcome)) {
    return "record";
  }
  if (advocacyStatus === "recording_only") {
    return "record";
  }

  const rawStage = Number(parcel.threat_stage);
  const stage = Number.isFinite(rawStage) ? Math.trunc(rawStage) : 0;
  const hasActiveStage = stage >= 1 && stage <= 5;
  const activeStage = hasActiveStage ? stage : 0;

  let hasUrgentHearing = false;
  if (stage !== 6 && asOfDateCst && nextHearingDateCst) {
    const deltaDays = calendarDaysBetween(asOfDateCst, nextHearingDateCst);
    if (
      deltaDays !== null &&
      deltaDays >= 0 &&
      deltaDays <= MONITORING_SCHEMA_CONTRACT.hearingUrgentWindowDays
    ) {
      hasUrgentHearing = true;
    }
  }
  const hasActiveThreat = hasActiveStage || hasUrgentHearing;

  const sigConfirmed = sanitizeMonitoringText(parcel.significance_level).toLowerCase();
  const sigRecommended = sanitizeMonitoringText(parcel.recommended_significance).toLowerCase();
  const revStatus = sanitizeMonitoringText(parcel.review_status).toLowerCase();
  const effSig =
    sigConfirmed ||
    sigRecommended ||
    (revStatus === "needs_research" ? "needs_research" : "");

  const protection =
    sanitizeMonitoringText(parcel.protection_level).toLowerCase() ||
    classifyProtectionLevel(parcel.designation_summary || "");

  if (effSig === "high" || effSig === "moderate" || effSig === "needs_research") {
    if (hasActiveThreat) {
      if (protection === "binding" && activeStage <= 3) {
        return "watch";
      }
      return "act";
    }
    return "watch";
  }

  const areaCtx = resolveEffectiveAreaContext(parcel.area_ids, areasById);
  const contributing =
    sanitizeMonitoringText(parcel.contributing_status).toLowerCase() ||
    computeContributingStatus(parcel, areaCtx);

  if (
    areaCtx.priorityStatus === "priority" &&
    contributing === "contributing" &&
    activeStage >= 3 &&
    activeStage <= 5
  ) {
    return "watch";
  }

  return "record";
}

export function explainAttentionLevel(
  parcel = {},
  { asOfDateCst = "", nextHearingDateCst = "", areasById = null } = {}
) {
  const level = computeAttentionLevel(parcel, { asOfDateCst, nextHearingDateCst, areasById });
  const override = sanitizeMonitoringText(parcel.attention_override).toLowerCase();
  if (override && VALID_ATTENTION_SET.has(override)) {
    return `${level.toUpperCase()} (Staff override)`;
  }
  const adv = sanitizeMonitoringText(parcel.advocacy_status).toLowerCase();
  const outcome = sanitizeMonitoringText(parcel.outcome).toLowerCase();
  if (adv === "closed" || TERMINAL_OUTCOMES_SET.has(outcome)) {
    return `Record: Case closed${outcome ? ` (${outcome.replace(/_/g, " ")})` : ""}`;
  }
  if (adv === "recording_only") {
    return "Record: Placed in Recording Only mode";
  }
  const stage = Math.max(0, Math.min(6, Math.trunc(Number(parcel.threat_stage) || 0)));
  const activeStage = stage >= 1 && stage <= 5 ? stage : 0;
  const stageLabel = MONITORING_SCHEMA_CONTRACT.enums.threatStages[String(stage)] || "None";
  let hasUrgentHearing = false;
  if (stage !== 6 && asOfDateCst && nextHearingDateCst) {
    const deltaDays = calendarDaysBetween(asOfDateCst, nextHearingDateCst);
    hasUrgentHearing =
      deltaDays !== null &&
      deltaDays >= 0 &&
      deltaDays <= MONITORING_SCHEMA_CONTRACT.hearingUrgentWindowDays;
  }
  const sig =
    sanitizeMonitoringText(parcel.significance_level) ||
    sanitizeMonitoringText(parcel.recommended_significance) ||
    (sanitizeMonitoringText(parcel.review_status) === "needs_research" ? "needs research" : "unreviewed");
  const prot =
    sanitizeMonitoringText(parcel.protection_level) ||
    classifyProtectionLevel(parcel.designation_summary || "");

  if (level === "act") {
    if (activeStage === 0 && hasUrgentHearing) {
      return `Act: ${sig.replace(/_/g, " ")} significance + upcoming hearing (${nextHearingDateCst})`;
    }
    return `Act: ${sig.replace(/_/g, " ")} significance + Stage ${activeStage} (${stageLabel})`;
  }
  if (level === "watch") {
    if (
      (sig === "high" || sig === "moderate" || sig === "needs research") &&
      prot === "binding" &&
      (activeStage >= 1 || hasUrgentHearing) &&
      activeStage <= 3
    ) {
      return activeStage >= 1
        ? `Watch: Binding HAHC review moderates Stage ${activeStage} (${stageLabel})`
        : `Watch: Binding HAHC review moderates upcoming hearing (${nextHearingDateCst})`;
    }
    if (sig === "high" || sig === "moderate" || sig === "needs research") {
      return `Watch: ${sig} significance with no active threat filing yet`;
    }
    return `Watch: Contributing parcel in Priority Area facing Stage ${activeStage} (${stageLabel})`;
  }
  return `Record: Logged for citywide survey and neighborhood metrics`;
}

/**
 * Migrate a legacy `Tier 1..4` Plat Review or Watchlist dossier into normalized parcel fields,
 * separating historic demolitions (`lost` / `lost_before_review`) from non-historic `not_relevant`.
 */
export function migrateLegacyTierToParcelFields(
  legacy = {},
  {
    isWatchlist = false,
    atlasKnownHigh = false,
    areaContext = null,
    defaultThreatStage = 0,
  } = {}
) {
  const tier = sanitizeMonitoringText(legacy.tier || "");
  const trackerStatus = sanitizeMonitoringText(legacy.trackerStatus || "");
  const notesText = [
    legacy.why || "",
    legacy.notes || "",
    legacy.removalReason || "",
    legacy.followup || "",
  ]
    .map(sanitizeMonitoringText)
    .join(" ")
    .toLowerCase();
  const yr = Number(legacy.yearBuilt ?? legacy.year_built ?? legacy.oldestYearBuilt ?? 0) || 0;
  const isTombstone =
    legacy.removedFromWatchlist === true ||
    trackerStatus === "Removed from Watchlist" ||
    trackerStatus === "Removed (Tier 3)" ||
    trackerStatus === "Removed (Tier 4)";

  let reviewStatus = "not_reviewed";
  let significanceLevel = "";
  let advocacyStatus = "monitoring";
  let outcome = "";
  let threatStage = Number(legacy.threatStage ?? defaultThreatStage) || 0;

  const needsResearchCue = /\b(?:needs\s+research|check\s+architect|story\s+check|verify\s+history|possible\s+landmark)\b/.test(
    notesText
  );
  const demolishedCue =
    Boolean(legacy.graduateToLost) ||
    trackerStatus === "Lost" ||
    /\b(?:demolished|already\s+gone|torn\s+down|razed|cleared\s+lot)\b/.test(notesText);
  const vacantNonHistoricCue =
    /\b(?:vacant\s+lot|never\s+historic|parking\s+lot|post[-\s]*1976|new\s+construction\s+only|irrelevant|mismatched)\b/.test(
      notesText
    );

  if (atlasKnownHigh) {
    reviewStatus = "assessed";
    significanceLevel = "high";
  }

  if (isTombstone) {
    advocacyStatus = "closed";
    const effCutoff =
      areaContext?.ageRuleCutoffYear || MONITORING_SCHEMA_CONTRACT.defaultAgeCutoffYear;
    outcome =
      demolishedCue && yr >= MONITORING_SCHEMA_CONTRACT.minStandingYear && yr <= effCutoff
        ? "lost"
        : "not_relevant";
    threatStage = 6;
  } else if (trackerStatus === "Lost" || legacy.graduateToLost === true) {
    reviewStatus = reviewStatus === "not_reviewed" ? "assessed" : reviewStatus;
    if (!significanceLevel && (tier === "1" || tier === "2")) {
      significanceLevel = tier === "1" ? "high" : "moderate";
    }
    advocacyStatus = "closed";
    outcome = isWatchlist ? "lost" : "lost_before_review";
    threatStage = 6;
  } else if (trackerStatus === "Saved") {
    reviewStatus = "assessed";
    significanceLevel = significanceLevel || (tier === "2" ? "moderate" : "high");
    advocacyStatus = "closed";
    outcome = "saved";
    threatStage = 6;
  } else if (tier === "1") {
    reviewStatus = needsResearchCue && !atlasKnownHigh ? "needs_research" : "assessed";
    significanceLevel = significanceLevel || "high";
    advocacyStatus =
      trackerStatus === "Monitoring" || trackerStatus === "For Sale"
        ? "monitoring"
        : "active_advocacy";
    if (threatStage === 0) threatStage = isWatchlist ? 3 : 3;
  } else if (tier === "2") {
    reviewStatus = needsResearchCue && !atlasKnownHigh ? "needs_research" : "assessed";
    significanceLevel = significanceLevel || "moderate";
    advocacyStatus = "monitoring";
    if (threatStage === 0 && !isWatchlist) threatStage = 3;
  } else if (tier === "3" || trackerStatus === "Archived (Tier 3)") {
    reviewStatus = "assessed";
    significanceLevel = significanceLevel || "not_significant";
    advocacyStatus = trackerStatus === "Archived (Tier 3)" ? "closed" : "recording_only";
    if (trackerStatus === "Archived (Tier 3)") {
      outcome = "no_longer_threatened";
      threatStage = 6;
    }
  } else if (tier === "4" || trackerStatus === "Archived (Tier 4)") {
    reviewStatus = "assessed";
    significanceLevel = significanceLevel || "not_significant";
    advocacyStatus = "closed";
    if (
      demolishedCue &&
      !vacantNonHistoricCue &&
      yr >= MONITORING_SCHEMA_CONTRACT.minStandingYear &&
      yr <= (areaContext?.ageRuleCutoffYear || MONITORING_SCHEMA_CONTRACT.defaultAgeCutoffYear)
    ) {
      outcome = isWatchlist ? "lost" : "lost_before_review";
    } else {
      outcome = "not_relevant";
    }
    threatStage = 6;
  } else if (trackerStatus === "For Sale" && threatStage === 0) {
    threatStage = 1;
  }

  const protectionLevel = classifyProtectionLevel(legacy.designation || "");
  const partialParcel = {
    year_built: yr || "",
    designation_summary: sanitizeMonitoringText(legacy.designation || ""),
    area_ids: areaContext?.areaIds || [],
    review_status: reviewStatus,
    recommended_significance: "",
    significance_level: significanceLevel,
    protection_level: protectionLevel,
    threat_stage: threatStage,
    advocacy_status: advocacyStatus,
    outcome,
    attention_override: "",
  };
  const contributingStatus = computeContributingStatus(partialParcel, areaContext);
  partialParcel.contributing_status = contributingStatus;
  const attentionLevel = computeAttentionLevel(partialParcel);

  return {
    review_status: reviewStatus,
    recommended_significance: "",
    significance_level: significanceLevel,
    contributing_status: contributingStatus,
    protection_level: protectionLevel,
    threat_stage: threatStage,
    advocacy_status: advocacyStatus,
    outcome,
    attention_level: attentionLevel,
  };
}

/**
 * Compute a read-only legacy `Tier 1..4` string (`""` if unreviewed) from normalized parcel fields
 * so `.docx`/`.csv` exports, legacy `t1..t4` URL parameters, and transition views stay 100% compatible.
 */
export function deriveLegacyTierFromParcel(parcel = {}) {
  if (!parcel || typeof parcel !== "object") return "";
  const outcome = sanitizeMonitoringText(parcel.outcome).toLowerCase();
  const adv = sanitizeMonitoringText(parcel.advocacy_status).toLowerCase();
  const rev = sanitizeMonitoringText(parcel.review_status).toLowerCase();
  const sig =
    sanitizeMonitoringText(parcel.significance_level).toLowerCase() ||
    sanitizeMonitoringText(parcel.recommended_significance).toLowerCase();
  const att =
    sanitizeMonitoringText(parcel.attention_level).toLowerCase() ||
    computeAttentionLevel(parcel);

  if (
    outcome === "not_relevant" ||
    outcome === "lost" ||
    outcome === "lost_before_review" ||
    (adv === "closed" && outcome !== "saved" && outcome !== "no_longer_threatened")
  ) {
    return "4";
  }
  if (adv === "recording_only") {
    return "3";
  }
  if (att === "act") {
    return sig === "moderate" && adv !== "active_advocacy" ? "2" : "1";
  }
  if (att === "watch") {
    return "2";
  }
  if (rev === "assessed" || outcome === "saved" || outcome === "no_longer_threatened") {
    return "3";
  }
  return "";
}

/**
 * Compact a `Decisions.history_json` array FIFO so serialized cell text stays strictly below
 * Google Sheets' safe `48,000`-character cell ceiling while preserving valid JSON and latest entries.
 */
export function compactHistoryJsonFifo(
  entries = [],
  maxChars = MONITORING_SCHEMA_CONTRACT.safeSheetCellCharLimit
) {
  const list = Array.isArray(entries)
    ? entries.map((e) => sanitizePayloadDashes(e)).filter((e) => e && typeof e === "object")
    : [];
  let kept = list.slice();
  let truncatedCount = 0;

  const encode = (items, dropped) =>
    JSON.stringify(
      dropped > 0 ? { truncatedCount: dropped, entries: items } : { entries: items }
    );

  let serialized = encode(kept, truncatedCount);
  while (serialized.length > maxChars && kept.length > 1) {
    kept.shift();
    truncatedCount += 1;
    serialized = encode(kept, truncatedCount);
  }
  if (serialized.length > maxChars && kept.length === 1) {
    const last = { ...kept[0] };
    if (typeof last.note === "string") {
      last.note = last.note.slice(0, Math.max(120, maxChars - 512));
    }
    kept = [last];
    serialized = encode(kept, truncatedCount);
  }
  return serialized;
}

/**
 * Compact a `Watchlist_Dossiers.dossier_json` payload FIFO below Google Sheets' safe `48,000`-char
 * cell limit while guaranteeing 100% valid JSON (never slicing raw JSON text mid-token).
 */
export function compactWatchlistDossierJson(
  dossierObj = {},
  maxChars = MONITORING_SCHEMA_CONTRACT.safeSheetCellCharLimit
) {
  const clean =
    dossierObj && typeof dossierObj === "object" && !Array.isArray(dossierObj)
      ? sanitizePayloadDashes(JSON.parse(JSON.stringify(dossierObj)))
      : {};
  let serialized = JSON.stringify(clean);
  if (serialized.length <= maxChars) return serialized;

  if (clean.photoDataUrl) {
    clean.photoDataUrl = "";
    clean.photoDataUrlOmittedInCompactCell = true;
    serialized = JSON.stringify(clean);
    if (serialized.length <= maxChars) return serialized;
  }

  if (Array.isArray(clean.findingsLog) && clean.findingsLog.length > 0) {
    clean.findingsLog = [];
    serialized = JSON.stringify(clean);
    if (serialized.length <= maxChars) return serialized;
  }

  let droppedChecks = Number(clean.truncatedCheckCount) || 0;
  while (serialized.length > maxChars && Array.isArray(clean.checklist)) {
    let targetRow = null;
    let maxLen = 1;
    for (const row of clean.checklist) {
      if (row && Array.isArray(row.checks) && row.checks.length > maxLen) {
        maxLen = row.checks.length;
        targetRow = row;
      }
    }
    if (!targetRow) break;
    targetRow.checks.shift();
    droppedChecks += 1;
    clean.truncatedCheckCount = droppedChecks;
    serialized = JSON.stringify(clean);
  }

  if (serialized.length > maxChars) {
    const textKeys = ["notes", "buildingHistory", "phPastActions"];
    for (const tk of textKeys) {
      if (serialized.length <= maxChars) break;
      if (typeof clean[tk] === "string" && clean[tk].length > 200) {
        const excess = serialized.length - maxChars + 256;
        clean[tk] = clean[tk].slice(0, Math.max(200, clean[tk].length - excess));
        serialized = JSON.stringify(clean);
      }
    }
  }
  return serialized;
}

function sanitizePayloadDashes(val) {
  if (typeof val === "string") return sanitizeMonitoringText(val);
  if (Array.isArray(val)) return val.map(sanitizePayloadDashes);
  if (val && typeof val === "object") {
    const out = {};
    for (const [k, v] of Object.entries(val)) {
      out[k] = sanitizePayloadDashes(v);
    }
    return out;
  }
  return val;
}

export function normalizeParcelRecord(raw = {}, { asOfDateCst = "", areasById = null } = {}) {
  const cleanHcad = normalizeHcad13(raw.hcad_num || raw.hcad || "");
  const relatedHcads = parseRelatedHcads([
    cleanHcad,
    ...(Array.isArray(raw.related_hcads) ? raw.related_hcads : parseDelimitedList(raw.related_hcads)),
  ]);
  const parcelId = resolveCanonicalParcelId({
    parcelId: raw.parcel_id,
    campusGroupId: raw.campus_group_id,
    hcadIds: relatedHcads,
    appNo: raw.latest_app_no || raw.app_no || "",
    address: raw.primary_address || raw.address || "",
    name: raw.building_name || "",
  });
  const areaIds = parseDelimitedList(raw.area_ids);
  const areaCtx = resolveEffectiveAreaContext(areaIds, areasById);

  const reviewStatusRaw = sanitizeMonitoringText(raw.review_status).toLowerCase();
  const reviewStatus = VALID_REVIEW_STATUS_SET.has(reviewStatusRaw)
    ? reviewStatusRaw
    : "not_reviewed";

  const recSigRaw = sanitizeMonitoringText(raw.recommended_significance).toLowerCase();
  const recSig = VALID_SIGNIFICANCE_SET.has(recSigRaw) ? recSigRaw : "";

  const confSigRaw = sanitizeMonitoringText(raw.significance_level).toLowerCase();
  const confSig = VALID_SIGNIFICANCE_SET.has(confSigRaw) ? confSigRaw : "";

  const bases = parseDelimitedList(raw.significance_basis)
    .map((b) => b.toLowerCase())
    .filter((b) => VALID_BASIS_SET.has(b));

  const contribOvRaw = sanitizeMonitoringText(raw.contributing_override).toLowerCase();
  const contribOv = VALID_CONTRIBUTING_SET.has(contribOvRaw) ? contribOvRaw : "";

  const integrityRaw = sanitizeMonitoringText(raw.integrity).toLowerCase();
  const integrity = VALID_INTEGRITY_SET.has(integrityRaw) ? integrityRaw : "";

  const desigSummary = sanitizeMonitoringText(raw.designation_summary || "");
  const protRaw = sanitizeMonitoringText(raw.protection_level).toLowerCase();
  const protectionLevel =
    protRaw && VALID_PROTECTION_SET.has(protRaw)
      ? protRaw
      : classifyProtectionLevel(desigSummary);

  const advRaw = sanitizeMonitoringText(raw.advocacy_status).toLowerCase();
  const advocacyStatus = VALID_ADVOCACY_SET.has(advRaw) ? advRaw : "monitoring";

  const outRaw = sanitizeMonitoringText(raw.outcome).toLowerCase();
  const outcome = VALID_OUTCOME_SET.has(outRaw) ? outRaw : "";

  const attOvRaw = sanitizeMonitoringText(raw.attention_override).toLowerCase();
  const attentionOverride = VALID_ATTENTION_SET.has(attOvRaw) ? attOvRaw : "";

  let threatStage = Math.max(0, Math.min(6, Math.trunc(Number(raw.threat_stage) || 0)));
  if (advocacyStatus === "closed" || TERMINAL_OUTCOMES_SET.has(outcome)) {
    threatStage = 6;
  }

  const yrNum = Number(raw.year_built) || 0;
  const hcadYrNum = Number(raw.hcad_year_built) || 0;

  const normalized = {
    parcel_id: parcelId,
    hcad_num: cleanHcad || relatedHcads[0] || "",
    campus_group_id: parcelId.startsWith("CAMPUS:")
      ? parcelId
      : sanitizeMonitoringText(raw.campus_group_id || ""),
    related_hcads: relatedHcads.join("; "),
    primary_address: sanitizeMonitoringText(raw.primary_address || raw.address || ""),
    perimeter_addresses: parseDelimitedList(raw.perimeter_addresses).join("; "),
    building_name: sanitizeMonitoringText(raw.building_name || ""),
    year_built:
      yrNum >= MONITORING_SCHEMA_CONTRACT.minStandingYear &&
      yrNum <= MONITORING_SCHEMA_CONTRACT.maxStandingYear
        ? yrNum
        : "",
    hcad_year_built:
      hcadYrNum >= MONITORING_SCHEMA_CONTRACT.minStandingYear &&
      hcadYrNum <= MONITORING_SCHEMA_CONTRACT.maxStandingYear
        ? hcadYrNum
        : "",
    subdivision_name: sanitizeMonitoringText(raw.subdivision_name || ""),
    area_ids: areaIds.join("; "),
    owner_of_record: sanitizeMonitoringText(raw.owner_of_record || ""),
    land_use_class: sanitizeMonitoringText(raw.land_use_class || ""),
    review_status: reviewStatus,
    recommended_significance: recSig,
    significance_level: confSig,
    significance_basis: bases.join("; "),
    contributing_status: "",
    contributing_override: contribOv,
    integrity,
    protection_level: protectionLevel,
    designation_summary: desigSummary,
    threat_stage: threatStage,
    signal_flags: parseDelimitedList(raw.signal_flags).join("; "),
    advocacy_status: advocacyStatus,
    outcome,
    attention_level: "",
    attention_override: attentionOverride,
    watch_property_id: sanitizeMonitoringText(raw.watch_property_id || ""),
    added_at: sanitizeMonitoringText(raw.added_at || ""),
    updated_by: sanitizeMonitoringText(raw.updated_by || ""),
    updated_at: sanitizeMonitoringText(raw.updated_at || ""),
  };

  normalized.contributing_status = computeContributingStatus(normalized, areaCtx);
  normalized.attention_level = computeAttentionLevel(normalized, {
    asOfDateCst,
    nextHearingDateCst: raw.next_hearing_date_cst || "",
    areasById,
  });

  return normalized;
}

/**
 * Column-partitioned row merger preventing concurrent daily sweeps (`actorRole === "pipeline"`)
 * or `Screener` recommendations from clobbering `Staff`-confirmed parcel judgments.
 */
export function mergeParcelAndDecisionConcurrent({
  existingParcel = null,
  existingDecision = null,
  patch = {},
  actorRole = "screener",
  actorLabel = "Preservation Staff",
  expectedUpdatedAt = "",
  nowIso = "",
  asOfDateCst = "",
  areasById = null,
} = {}) {
  const role = sanitizeMonitoringText(actorRole).toLowerCase();
  if (!["pipeline", "screener", "staff"].includes(role)) {
    return { ok: false, error: `Unauthorized actorRole: ${actorRole}`, conflict: false };
  }

  const ts = sanitizeMonitoringText(nowIso) || new Date().toISOString();
  const baseParcel = existingParcel ? { ...existingParcel } : {};
  const baseDecision = existingDecision ? { ...existingDecision } : {};

  if (
    expectedUpdatedAt &&
    baseDecision.decided_at &&
    sanitizeMonitoringText(expectedUpdatedAt) !== sanitizeMonitoringText(baseDecision.decided_at) &&
    role !== "pipeline"
  ) {
    return {
      ok: false,
      conflict: true,
      error: "Parcel decision was updated in another session; reload or merge latest changes.",
      currentParcel: normalizeParcelRecord(baseParcel, { asOfDateCst, areasById }),
      currentDecision: baseDecision,
    };
  }

  if (role === "pipeline") {
    const mergedRaw = { ...baseParcel };
    if (!existingParcel) {
      for (const [k, v] of Object.entries(patch)) {
        if (v !== undefined) mergedRaw[k] = v;
      }
      mergedRaw.added_at = mergedRaw.added_at || ts;
    } else {
      for (const col of PIPELINE_MACHINE_PARCEL_COLS) {
        if (patch[col] !== undefined) {
          mergedRaw[col] = patch[col];
        }
      }
    }
    mergedRaw.updated_at = ts;
    const nextParcel = normalizeParcelRecord(mergedRaw, {
      asOfDateCst: asOfDateCst || toChicagoCalendarDate(ts),
      areasById,
    });
    return {
      ok: true,
      conflict: false,
      parcel: nextParcel,
      decision: existingDecision ? { ...baseDecision } : null,
    };
  }

  // Human ("screener" or "staff") decision merge
  for (const staffCol of STAFF_ONLY_DECISION_COLS) {
    if (patch[staffCol] !== undefined && role !== "staff") {
      return {
        ok: false,
        conflict: false,
        forbiddenField: staffCol,
        error: `Role '${role}' cannot modify staff-only field '${staffCol}'.`,
      };
    }
  }

  const nextDecision = {
    decision_id:
      baseDecision.decision_id ||
      `DEC:${resolveCanonicalParcelId({
        parcelId: patch.parcel_id || baseParcel.parcel_id,
        campusGroupId: patch.campus_group_id || baseParcel.campus_group_id,
        hcadIds: [patch.hcad_num || baseParcel.hcad_num || ""],
        appNo: patch.latest_app_no || "",
        address: patch.primary_address || baseParcel.primary_address || "",
      })}`,
    parcel_id: baseDecision.parcel_id || baseParcel.parcel_id || patch.parcel_id || "",
    hcad_num: normalizeHcad13(patch.hcad_num || baseDecision.hcad_num || baseParcel.hcad_num || ""),
    latest_app_no: sanitizeMonitoringText(patch.latest_app_no ?? baseDecision.latest_app_no ?? ""),
    latest_cycle_key: sanitizeMonitoringText(
      patch.latest_cycle_key ?? baseDecision.latest_cycle_key ?? ""
    ),
    actor_role: role,
    review_status: sanitizeMonitoringText(
      patch.review_status ?? baseDecision.review_status ?? baseParcel.review_status ?? "not_reviewed"
    ),
    recommended_significance: sanitizeMonitoringText(
      patch.recommended_significance ??
        baseDecision.recommended_significance ??
        baseParcel.recommended_significance ??
        ""
    ),
    significance_level:
      role === "staff" && patch.significance_level !== undefined
        ? sanitizeMonitoringText(patch.significance_level)
        : sanitizeMonitoringText(
            baseDecision.significance_level ?? baseParcel.significance_level ?? ""
          ),
    significance_basis:
      patch.significance_basis !== undefined
        ? parseDelimitedList(patch.significance_basis).join("; ")
        : sanitizeMonitoringText(
            baseDecision.significance_basis ?? baseParcel.significance_basis ?? ""
          ),
    contributing_override:
      role === "staff" && patch.contributing_override !== undefined
        ? sanitizeMonitoringText(patch.contributing_override)
        : sanitizeMonitoringText(
            baseDecision.contributing_override ?? baseParcel.contributing_override ?? ""
          ),
    integrity: sanitizeMonitoringText(
      patch.integrity ?? baseDecision.integrity ?? baseParcel.integrity ?? ""
    ),
    standing_status: sanitizeMonitoringText(
      patch.standing_status ?? baseDecision.standing_status ?? ""
    ),
    advocacy_status:
      role === "staff" && patch.advocacy_status !== undefined
        ? sanitizeMonitoringText(patch.advocacy_status)
        : sanitizeMonitoringText(
            baseDecision.advocacy_status ?? baseParcel.advocacy_status ?? "monitoring"
          ),
    outcome:
      role === "staff" && patch.outcome !== undefined
        ? sanitizeMonitoringText(patch.outcome)
        : sanitizeMonitoringText(baseDecision.outcome ?? baseParcel.outcome ?? ""),
    attention_override:
      role === "staff" && patch.attention_override !== undefined
        ? sanitizeMonitoringText(patch.attention_override)
        : sanitizeMonitoringText(
            baseDecision.attention_override ?? baseParcel.attention_override ?? ""
          ),
    screening_notes: sanitizeMonitoringText(
      patch.screening_notes ?? baseDecision.screening_notes ?? ""
    ),
    story_check_json:
      typeof patch.story_check_json === "object" && patch.story_check_json !== null
        ? JSON.stringify(sanitizePayloadDashes(patch.story_check_json))
        : sanitizeMonitoringText(patch.story_check_json ?? baseDecision.story_check_json ?? ""),
    atlas_writeback_summary:
      role === "staff" && patch.atlas_writeback_summary !== undefined
        ? sanitizeMonitoringText(patch.atlas_writeback_summary)
        : sanitizeMonitoringText(baseDecision.atlas_writeback_summary ?? ""),
    decided_by: sanitizeMonitoringText(actorLabel || "Preservation Staff"),
    decided_at: ts,
    history_json: "",
  };

  let priorHistory = [];
  if (baseDecision.history_json) {
    try {
      const parsedHist = JSON.parse(baseDecision.history_json);
      if (Array.isArray(parsedHist)) priorHistory = parsedHist;
      else if (Array.isArray(parsedHist?.entries)) priorHistory = parsedHist.entries;
    } catch (_e) {
      priorHistory = [];
    }
  }
  priorHistory.push({
    at: ts,
    role,
    by: nextDecision.decided_by,
    review_status: nextDecision.review_status,
    recommended_significance: nextDecision.recommended_significance,
    significance_level: nextDecision.significance_level,
    advocacy_status: nextDecision.advocacy_status,
    outcome: nextDecision.outcome,
    note: nextDecision.screening_notes.slice(0, 280),
  });
  nextDecision.history_json = compactHistoryJsonFifo(priorHistory);

  const mergedParcelRaw = {
    ...baseParcel,
    ...Object.fromEntries(
      Object.entries(patch).filter(
        ([k, v]) =>
          v !== undefined &&
          !STAFF_ONLY_DECISION_COLS.has(k) &&
          !SCREENER_DECISION_COLS.has(k)
      )
    ),
    parcel_id: nextDecision.parcel_id || baseParcel.parcel_id || patch.parcel_id || "",
    hcad_num: nextDecision.hcad_num || baseParcel.hcad_num || "",
    review_status: nextDecision.review_status,
    recommended_significance: nextDecision.recommended_significance,
    significance_level: nextDecision.significance_level,
    significance_basis: nextDecision.significance_basis,
    contributing_override: nextDecision.contributing_override,
    integrity: nextDecision.integrity,
    advocacy_status: nextDecision.advocacy_status,
    outcome: nextDecision.outcome,
    attention_override: nextDecision.attention_override,
    added_at: baseParcel.added_at || ts,
    updated_by: nextDecision.decided_by,
    updated_at: ts,
  };

  const nextParcel = normalizeParcelRecord(mergedParcelRaw, {
    asOfDateCst: asOfDateCst || toChicagoCalendarDate(ts),
    areasById,
  });
  nextDecision.parcel_id = nextParcel.parcel_id;
  nextDecision.decision_id = `DEC:${nextParcel.parcel_id}`;

  return {
    ok: true,
    conflict: false,
    parcel: nextParcel,
    decision: nextDecision,
  };
}
