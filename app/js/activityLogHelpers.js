/**
 * Pure, zero-DOM helpers for the "What's New & Daily Log" 3rd workspace mode in plat-monitor.html.
 *
 * Architectural Contracts:
 *   1. Cross-Language Canonical Event ID Parity (DF-2):
 *      All normalization helpers (`sanitizeActivityText`, `collapseUpper`, `fnv1a32Hex`,
 *      `slugToken`, `normalizeHcad13`) and canonical ID builders (`buildAutoCheckEventId`,
 *      `buildManualCheckEventId`, `buildPermitCohEventId`, `buildHahcFilingEventId`, etc.) mirror
 *      `pipeline/atlas_pipeline/build_monitor_activity_log.py` byte-for-byte so static CI events
 *      and live client / Google Sheet delta events deduplicate cleanly without ID drift.
 *   2. Temporal Boundaries & Non-Resurrection (DF-1):
 *      `synthesizeLiveMonitorEvents` gates candidate live mutations strictly behind BOTH the
 *      90-day `America/Chicago` retention floor AND `staticLog.generatedAt` (using numeric epoch
 *      comparison so `+00:00` and `Z` offsets compare accurately), preventing >90-day archive rows
 *      (e.g. 400+ historical HAHC filings) from resurrecting as unread events while immediately
 *      surfacing backdated manual checks that carry a post-watermark `loggedAt` ISO timestamp.
 *      When `staticLog` fails to load (HTTP 404/offline), synthesis degrades smoothly to a bounded
 *      7-day rolling fallback window.
 *   3. Exhaustive 3-Mode Boot Routing (DF-4):
 *      `resolveInitialMonitorMode` enforces strict URL parameter precedence (`?mode=`, `?tab=`,
 *      `?watch=`, `?hcad=`, `?address=`, `?cycle=`, `?app=`, `?id=`) over `phpam:lastMode`,
 *      defaulting to `"agenda"` in clean sessions.
 *   4. Honest Sweep Health Visibility (DF-3 / DF-5):
 *      `compactDailyRunsForLedger` collapses contiguous zero-event healthy days while NEVER hiding
 *      any daily run where `failedAgencies.length > 0` or `allOk === false`.
 */

import { parseHcadAccountList } from "./platMonitorHelpers.js?v=20261030b";
import {
  REMOVED_TIER3_TOMBSTONE,
  REMOVED_WATCHLIST_TOMBSTONE,
  escapeHtmlText,
  findChecklistRow,
  isRemovedWatchlistTombstone,
  normalizeWatchProperty,
  sanitizeExternalLinksList,
  simplifySearchAddress,
} from "./watchlistSyncHelpers.js?v=20261030b";

export const ACTIVITY_RETENTION_DAYS = 90;
export const MAX_STORED_READ_EVENT_IDS = 500;
export const MONITOR_MODES = ["agenda", "watchlist", "activity"];

export const ACTIVITY_STREAM_META = {
  watchlist: { label: "Watchlist Sweeps", shortLabel: "Watchlist" },
  agenda: { label: "Plat Agendas", shortLabel: "Plat Agenda" },
  staff: { label: "Staff Edits", shortLabel: "Staff" },
  permits: { label: "Permits & HAHC", shortLabel: "Permits/HAHC" },
};

export const AUTOMATED_SWEEP_AGENCY_LABELS = {
  "chk-04": "TX Comptroller (Sales & Franchise Tax)",
  "chk-05": "HCAD Ownership & Classification",
  "chk-06": "TABC Liquor Licensing",
  "chk-09": "COH Planning Commission Plats & Variances",
  "chk-10": "TDLR Architectural Barriers (TABS)",
  "chk-13": "Houston 311 & Dangerous Buildings",
  "chk-14": "Houston Sold Permits",
  "chk-15": "TX DSHS Asbestos & Demolition",
};

const HIGH_SEVERITY_CHECK_IDS = new Set(["chk-10", "chk-13", "chk-14", "chk-15"]);
const SEVERITY_RANK = { critical: 0, warning: 1, info: 2 };

const chicagoDateFormatter = new Intl.DateTimeFormat("en-CA", {
  timeZone: "America/Chicago",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

const utf8Encoder = new TextEncoder();

// ---------------------------------------------------------------------------
// 1. Cross-Language Normalization & Canonical Event ID Builders (DF-2)
// ---------------------------------------------------------------------------

export function sanitizeActivityText(val) {
  return String(val ?? "")
    .replace(/\u2014/g, " - ")
    .replace(/\u2013/g, "-")
    .trim();
}

export function collapseUpper(raw) {
  return sanitizeActivityText(raw)
    .toUpperCase()
    .split(/\s+/)
    .filter(Boolean)
    .join(" ");
}

export function fnv1a32Hex(text) {
  let h = 0x811c9dc5;
  const bytes = utf8Encoder.encode(String(text || ""));
  for (let i = 0; i < bytes.length; i += 1) {
    h ^= bytes[i];
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h.toString(16).padStart(8, "0");
}

export function slugToken(raw) {
  const cleaned = sanitizeActivityText(raw)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return cleaned.slice(0, 64) || "none";
}

export function normalizeHcad13(raw) {
  const text = String(raw ?? "").trim();
  if (!text) return "nohcad";
  const parts = text.split(/[,;\s|]+/);
  for (const rawTok of parts) {
    const tok = rawTok.replace(/^#+/, "").split("#")[0].trim();
    if (!tok || /^[+-]?\d+(?:\.\d+)?E[+-]?\d+$/i.test(tok)) continue;
    const digits = tok.replace(/[-\s]+/g, "");
    if (/^\d{10,13}$/.test(digits) && !/^0+$/.test(digits)) {
      return digits.padStart(13, "0");
    }
  }
  return slugToken(text);
}

export function parseIsoDatePrefix(val) {
  const m = String(val ?? "")
    .trim()
    .match(/^(\d{4}-\d{2}-\d{2})/);
  return m ? m[1] : "";
}

export function parseIsoEpochMs(ts) {
  const raw = String(ts ?? "").trim();
  if (!raw) return null;
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) {
    const ms = Date.parse(`${raw}T12:00:00Z`);
    return Number.isFinite(ms) ? ms : null;
  }
  let normalized = raw;
  // Normalize Python microsecond offsets and tz-naive ISO strings cleanly
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/.test(normalized)) {
    normalized += "Z";
  }
  const ms = Date.parse(normalized);
  return Number.isFinite(ms) ? ms : null;
}

export function isIsoStrictlyAfter(candidateIso, baselineIso) {
  const cMs = parseIsoEpochMs(candidateIso);
  const bMs = parseIsoEpochMs(baselineIso);
  if (cMs === null || bMs === null) return false;
  return cMs > bMs;
}

export function toChicagoDateIso(tsOrDate, fallbackIso = "") {
  const raw = String(tsOrDate ?? "").trim() || String(fallbackIso ?? "").trim();
  if (!raw) return "";
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) return raw;
  const ms = parseIsoEpochMs(raw);
  if (ms !== null) {
    const formatted = chicagoDateFormatter.format(new Date(ms));
    if (/^\d{4}-\d{2}-\d{2}$/.test(formatted)) return formatted;
  }
  return parseIsoDatePrefix(raw);
}

export function shiftIsoDate(isoDate, deltaDays) {
  const clean = parseIsoDatePrefix(isoDate);
  if (!clean) return "";
  const [y, m, d] = clean.split("-").map(Number);
  const utc = new Date(Date.UTC(y, m - 1, d + Number(deltaDays || 0)));
  return utc.toISOString().slice(0, 10);
}

export function isAutoSweepCheck(chk) {
  if (!chk || typeof chk !== "object") return false;
  if (String(chk.source || "").trim() === "auto-sweep") return true;
  if (chk.automated === true) return true;
  return String(chk.result || "")
    .trim()
    .toUpperCase()
    .startsWith("[AUTO-SWEEP]");
}

export function isPlatSeedCheck(chk) {
  return Boolean(chk && typeof chk === "object" && String(chk.source || "").trim() === "plat-seed");
}

export function isAdverseAutoSweepCheck(chk) {
  if (!isAutoSweepCheck(chk)) return false;
  if (typeof chk.adverse === "boolean") return chk.adverse;
  const text = String(chk.result || "");
  if (
    /\b(?:no new|0 active|no matching|none found|confirmed match to current owner)\b/i.test(text)
  ) {
    return false;
  }
  return true;
}

export function buildAutoCheckEventId(propId, rowId, chk = {}) {
  const pid = String(propId || "").trim();
  const rid = String(rowId || "").trim().toLowerCase();
  const agencyRecordId = String(chk?.agencyRecordId || "").trim();
  if (agencyRecordId) {
    return `wl:auto:${pid}:${rid}:id:${agencyRecordId}`;
  }
  return `wl:auto:${pid}:${rid}:txt:${fnv1a32Hex(collapseUpper(chk?.result))}`;
}

export function buildManualCheckEventId(propId, rowId, chk = {}) {
  const pid = String(propId || "").trim();
  const rid = String(rowId || "").trim().toLowerCase();
  const dIso = parseIsoDatePrefix(chk?.date) || "nodate";
  return `wl:manual:${pid}:${rid}:${dIso}:${fnv1a32Hex(collapseUpper(chk?.result))}`;
}

export function buildWatchStatusEventId(propId, rowId, status, dateIso) {
  const pid = String(propId || "").trim();
  const rid = String(rowId || "").trim().toLowerCase();
  return `wl:status:${pid}:${rid}:${slugToken(status)}:${dateIso || "nodate"}`;
}

export function buildSweepDegradedEventId(localDate, failedAgencies = []) {
  const cleanDate = parseIsoDatePrefix(localDate) || String(localDate || "").trim() || "nodate";
  const sorted = Array.from(
    new Set(
      (Array.isArray(failedAgencies) ? failedAgencies : [])
        .map((x) => String(x || "").trim().toLowerCase())
        .filter(Boolean)
    )
  ).sort();
  return `wl:sweep:${cleanDate}:degraded:${sorted.join("-") || "unknown"}`;
}

export function buildStaffPropEventId(propId, action, dateIso = "") {
  const pid = String(propId || "").trim();
  const act = slugToken(action);
  if (act === "edited") {
    return `staff:prop:${pid}:edited:${dateIso || "nodate"}`;
  }
  return `staff:prop:${pid}:${act}`;
}

export function buildStaffReviewEventId(cycleKey, appNo, tier) {
  const t = String(tier || "0").trim() || "0";
  return `staff:review:${String(cycleKey || "").trim()}:${String(appNo || "").trim()}:t${t}`;
}

export function buildAgendaCycleEventId(cycleKey) {
  return `agenda:cycle:${String(cycleKey || "").trim()}`;
}

export function buildAgendaItemEventId(cycleKey, appNo, kind) {
  return `agenda:item:${String(cycleKey || "").trim()}:${String(appNo || "").trim()}:${kind}`;
}

export function buildPermitCohEventId(hcadRaw, projectNo, fallbackDesc = "", dateIso = "") {
  const key = String(projectNo ?? "").trim()
    ? slugToken(projectNo)
    : slugToken(`${fallbackDesc}-${dateIso}`);
  return `permit:coh:${normalizeHcad13(hcadRaw)}:${key}`;
}

export function buildPermitDshsEventId(hcadRaw, notifNo, fallbackAddr = "", dateIso = "") {
  const key = String(notifNo ?? "").trim()
    ? slugToken(notifNo)
    : slugToken(`${fallbackAddr}-${dateIso}`);
  return `permit:dshs:${normalizeHcad13(hcadRaw)}:${key}`;
}

export function buildHahcCycleEventId(meetingDate) {
  return `hahc:cycle:${String(meetingDate || "").trim()}`;
}

export function buildHahcGuidelineEventId(meetingDate, titleOrUrl) {
  return `hahc:guideline:${String(meetingDate || "").trim()}:${slugToken(titleOrUrl)}`;
}

export function buildHahcFilingEventId(filingId, outcome) {
  return `hahc:filing:${String(filingId || "").trim()}:${slugToken(outcome || "pending_agenda")}`;
}

// ---------------------------------------------------------------------------
// 2. Boot Mode Precedence & URL Deep-Link Resolution (DF-4)
// ---------------------------------------------------------------------------

function normalizeModeToken(raw) {
  const tok = String(raw ?? "").trim().toLowerCase();
  if (tok === "activity" || tok === "log" || tok === "whatsnew" || tok === "briefing") {
    return "activity";
  }
  if (tok === "watchlist" || tok === "watch") {
    return "watchlist";
  }
  if (tok === "agenda" || tok === "plats" || tok === "plat") {
    return "agenda";
  }
  return "";
}

/**
 * Resolve the initial Plat & Property Monitor tab with strict URL parameter authority:
 *   explicit `?mode=` / `?tab=` > `?watch=` / `?hcad=` / `?address=` > `?cycle=` / `?app=` / `?id=`
 *   > `storedLastMode` (`phpam:lastMode`) > `"agenda"` default.
 */
export function resolveInitialMonitorMode({ searchString = "", storedLastMode = "" } = {}) {
  const rawSearch = String(searchString || "").replace(/^\?/, "");
  const params = new URLSearchParams(rawSearch);

  const explicitMode = normalizeModeToken(params.get("mode") || params.get("tab") || "");
  if (explicitMode) {
    return { mode: explicitMode, explicitUrlOverride: true, reason: "mode_param" };
  }

  if (
    String(params.get("watch") || "").trim() ||
    String(params.get("hcad") || "").trim() ||
    String(params.get("address") || "").trim()
  ) {
    return { mode: "watchlist", explicitUrlOverride: true, reason: "watch_deeplink" };
  }

  if (
    String(params.get("cycle") || "").trim() ||
    String(params.get("app") || "").trim() ||
    String(params.get("id") || "").trim()
  ) {
    return { mode: "agenda", explicitUrlOverride: true, reason: "agenda_deeplink" };
  }

  const normalizedStored = normalizeModeToken(storedLastMode);
  if (normalizedStored && MONITOR_MODES.includes(normalizedStored)) {
    return { mode: normalizedStored, explicitUrlOverride: false, reason: "stored_last_mode" };
  }

  return { mode: "agenda", explicitUrlOverride: false, reason: "default" };
}

// ---------------------------------------------------------------------------
// 3. Live Event Synthesis & Ledger Merge (DF-1, DF-2, DF-5)
// ---------------------------------------------------------------------------

function formatHahcOutcomeLabel(outcomeRaw) {
  const slug = slugToken(outcomeRaw || "pending_agenda");
  const mapping = {
    "pending-agenda": "Pending HAHC Agenda",
    approved: "Approved",
    "approved-conditions": "Approved with Conditions",
    "denied-or-cor": "Denied / Certificate of Remediation",
    denied: "Denied",
    deferred: "Deferred",
    withdrawn: "Withdrawn",
  };
  return mapping[slug] || sanitizeActivityText(outcomeRaw || "Pending HAHC Agenda");
}

function makeActivityEvent({
  id,
  stream,
  kind,
  severity = "info",
  adverse = false,
  dateIso = "",
  timestampIso = "",
  discoveredAt = "",
  loggedAt = "",
  title = "",
  summary = "",
  address = "",
  hcad = "",
  watchPropertyId = "",
  checkId = "",
  cycleKey = "",
  appNo = "",
  occKey = "",
  buildingId = "",
  lat = null,
  lng = null,
  yearBuilt = null,
  designation = "",
  links = [],
  permitMeta = null,
  syntheticLive = false,
}) {
  const effectiveTs = loggedAt || discoveredAt || timestampIso;
  const localDate = toChicagoDateIso(effectiveTs, dateIso) || dateIso;
  let normHcad = String(hcad || "").trim() ? normalizeHcad13(hcad) : "";
  if (normHcad === "nohcad" || !/^\d+$/.test(normHcad)) {
    normHcad = sanitizeActivityText(hcad);
  }
  const out = {
    id: String(id || "").trim(),
    stream,
    kind,
    severity: severity === "critical" || severity === "warning" ? severity : "info",
    adverse: Boolean(adverse),
    date: dateIso || localDate,
    localDate,
    timestamp: timestampIso,
    discoveredAt: discoveredAt || timestampIso,
    loggedAt: loggedAt || "",
    title: sanitizeActivityText(title),
    summary: sanitizeActivityText(summary),
    address: sanitizeActivityText(address),
    hcad: normHcad,
    watchPropertyId: String(watchPropertyId || "").trim(),
    checkId: String(checkId || "").trim().toLowerCase(),
    cycleKey: String(cycleKey || "").trim(),
    appNo: String(appNo || "").trim(),
    occKey: String(occKey || "").trim(),
    buildingId: String(buildingId || "").trim(),
    lat: Number.isFinite(Number(lat)) && lat !== null && lat !== "" ? Number(lat) : null,
    lng: Number.isFinite(Number(lng)) && lng !== null && lng !== "" ? Number(lng) : null,
    yearBuilt:
      yearBuilt !== null && yearBuilt !== undefined && yearBuilt !== "" && String(yearBuilt) !== "0"
        ? yearBuilt
        : null,
    designation: sanitizeActivityText(designation),
    links: sanitizeExternalLinksList(links),
  };
  if (permitMeta && typeof permitMeta === "object") {
    out.permitMeta = permitMeta;
  }
  if (syntheticLive) {
    out.syntheticLive = true;
  }
  return out;
}

export function getEventEffectiveTimestamp(ev) {
  if (!ev || typeof ev !== "object") return "";
  return String(
    ev.loggedAt ||
      ev.discoveredAt ||
      ev.timestamp ||
      (ev.localDate ? `${ev.localDate}T12:00:00Z` : ev.date ? `${ev.date}T12:00:00Z` : "")
  ).trim();
}

function buildPermitAlertMeta(alert = {}, alertKind = "coh", record = {}) {
  return {
    alertKind,
    title: sanitizeActivityText(alert.title || alert.building_name || alert.address || ""),
    address: sanitizeActivityText(alert.address || ""),
    hcad_num: normalizeHcad13(alert.hcad_num || ""),
    related_hcads: Array.isArray(alert.related_hcads)
      ? alert.related_hcads.map((x) => String(x || "").trim()).filter(Boolean)
      : [],
    watchlist_id: String(alert.watchlist_id || "").trim(),
    building_id: String(alert.building_id || "").trim(),
    year_built: alert.year_built ?? null,
    yr_remodel: alert.yr_remodel ?? null,
    architect: sanitizeActivityText(alert.architect || ""),
    style: sanitizeActivityText(alert.style || ""),
    neighborhood: sanitizeActivityText(alert.neighborhood || ""),
    historic_district: sanitizeActivityText(alert.historic_district || ""),
    landmark_name: sanitizeActivityText(alert.landmark_name || ""),
    good_brick_summary: sanitizeActivityText(alert.good_brick_summary || ""),
    owner: sanitizeActivityText(alert.owner || ""),
    lat: alert.lat ?? null,
    lng: alert.lng ?? alert.lon ?? null,
    record: { ...(record || {}) },
  };
}

function sortEventsLedgerOrder(events) {
  return events.slice().sort((a, b) => {
    const ldCmp = String(b.localDate || "").localeCompare(String(a.localDate || ""));
    if (ldCmp !== 0) return ldCmp;
    const msA = parseIsoEpochMs(getEventEffectiveTimestamp(a)) ?? 0;
    const msB = parseIsoEpochMs(getEventEffectiveTimestamp(b)) ?? 0;
    if (msA !== msB) return msB - msA;
    const sevA = SEVERITY_RANK[a.severity] ?? 2;
    const sevB = SEVERITY_RANK[b.severity] ?? 2;
    if (sevA !== sevB) return sevA - sevB;
    return String(b.id || "").localeCompare(String(a.id || ""));
  });
}

export function isNotableNewCycleItem(it, reviews = {}) {
  if (!it || typeof it !== "object") return false;
  const app = String(it.app || "").trim();
  const section = String(it.section || "");
  const typ = String(it.type || "");
  const isDpv =
    app.toUpperCase().startsWith("DPV") ||
    section.includes("K.") ||
    typ.toUpperCase().includes("DPV");
  if (isDpv || it.noticePdf) return true;
  const rev = reviews && typeof reviews === "object" ? reviews[app] : null;
  if (
    rev &&
    typeof rev === "object" &&
    rev.tierConfirmed &&
    (String(rev.tier || "") === "1" || String(rev.tier || "") === "2")
  ) {
    return true;
  }
  const cls = it._c;
  // Minimal synthetic unit-test items without `_c` are always surfaced
  if (!cls || typeof cls !== "object") return true;
  const grp = String(cls.group || "");
  if ((grp === "out" || grp === "etj") && !cls.overriddenFromOut) return false;
  if (grp === "designated") return true;
  const atlas = it.atlas && typeof it.atlas === "object" ? it.atlas : {};
  if (
    atlas.hasActivePermit ||
    (Array.isArray(atlas.designations) && atlas.designations.length > 0)
  ) {
    return true;
  }
  const yr = Number(atlas.earliestYearBuilt || 0);
  if (yr >= 1836 && yr <= 1940) return true;
  if (
    yr >= 1941 &&
    yr <= 1976 &&
    (String(it.special || "").toLowerCase().includes("variance") ||
      section.includes("D.") ||
      section.includes("F.") ||
      Boolean(it.deferral))
  ) {
    return true;
  }
  return false;
}

/**
 * Merge static CI `monitor_activity_log.json` records with genuinely post-baseline live browser
 * mutations (Google Sheets rows, localStorage edits, manual `loggedAt` check stamps, and live
 * Plat/Permit/HAHC additions), strictly bounded by `retentionDays` in `America/Chicago`.
 */
export function synthesizeLiveMonitorEvents({
  staticLog = null,
  watchedProperties = [],
  watchlistSweepHealth = null,
  platState = null,
  permitAlertsPayload = null,
  hahcPayload = null,
  nowIso = "",
  retentionDays = ACTIVITY_RETENTION_DAYS,
} = {}) {
  const effectiveNowIso = String(nowIso || "").trim() || new Date().toISOString();
  const todayLocal = toChicagoDateIso(effectiveNowIso);
  const retentionFloorDate = shiftIsoDate(todayLocal, -(Math.max(1, Number(retentionDays) || 90) - 1));
  const fallback7dFloorDate = shiftIsoDate(todayLocal, -6);

  const hasStaticLog = Boolean(
    staticLog && typeof staticLog === "object" && Array.isArray(staticLog.events)
  );
  const ledgerBaselineIso = hasStaticLog
    ? String(staticLog.generatedAt || staticLog.lastSweepAt || "").trim()
    : "";
  const baselineLocalDate = ledgerBaselineIso ? toChicagoDateIso(ledgerBaselineIso) : "";

  const eventsById = new Map();
  if (hasStaticLog) {
    for (const rawEv of staticLog.events) {
      if (!rawEv || typeof rawEv !== "object" || !rawEv.id) continue;
      const ld =
        parseIsoDatePrefix(rawEv.localDate) ||
        toChicagoDateIso(getEventEffectiveTimestamp(rawEv), rawEv.date || "");
      if (!ld || ld < retentionFloorDate) continue;
      eventsById.set(String(rawEv.id), {
        ...rawEv,
        localDate: ld,
        title: sanitizeActivityText(rawEv.title),
        summary: sanitizeActivityText(rawEv.summary),
        address: sanitizeActivityText(rawEv.address),
        designation: sanitizeActivityText(rawEv.designation),
        links: sanitizeExternalLinksList(rawEv.links),
      });
    }
  }

  const passesTemporalGate = (mutationTs, candidateLocalDate) => {
    const cleanDate = parseIsoDatePrefix(candidateLocalDate);
    if (!cleanDate || cleanDate < retentionFloorDate) return false;
    if (hasStaticLog && ledgerBaselineIso) {
      if (!mutationTs) return false;
      return isIsoStrictlyAfter(mutationTs, ledgerBaselineIso);
    }
    return cleanDate >= fallback7dFloorDate;
  };

  const tryAddSynthetic = (ev) => {
    if (!ev || !ev.id || eventsById.has(ev.id)) return;
    if (!ev.localDate || ev.localDate < retentionFloorDate) return;
    // Clamp any future docket date so no synthetic event lands after todayLocal in the run ledger
    if (todayLocal && ev.localDate > todayLocal) {
      ev.localDate = todayLocal;
    }
    eventsById.set(ev.id, ev);
  };

  // --- Stream 1: Live sweep degraded notice if watchlist payload reports failed agencies ---
  const activeSweepHealth =
    (watchlistSweepHealth && typeof watchlistSweepHealth === "object" ? watchlistSweepHealth : null) ||
    (staticLog?.lastSweepHealth && typeof staticLog.lastSweepHealth === "object"
      ? staticLog.lastSweepHealth
      : null);
  if (activeSweepHealth) {
    const sweepTs = String(activeSweepHealth.timestamp || effectiveNowIso);
    const sweepDay = toChicagoDateIso(sweepTs, todayLocal) || todayLocal;
    const failedIds = (Array.isArray(activeSweepHealth.failedAgencies)
      ? activeSweepHealth.failedAgencies
      : []
    )
      .map((x) => String(x || "").trim().toLowerCase())
      .filter(Boolean);
    const targetEvId = failedIds.length > 0 ? buildSweepDegradedEventId(sweepDay, failedIds) : "";
    for (const [existingId, existingEv] of Array.from(eventsById.entries())) {
      if (
        existingEv?.localDate === sweepDay &&
        existingEv?.kind === "watchlist_sweep_degraded" &&
        existingId !== targetEvId
      ) {
        eventsById.delete(existingId);
      }
    }
    if (failedIds.length > 0) {
      const evId = targetEvId;
      if (!eventsById.has(evId) && sweepDay >= retentionFloorDate) {
        const agenciesMeta = activeSweepHealth.agencies || {};
        const detailParts = failedIds.map((cid) => {
          const info = agenciesMeta[cid] || {};
          const lbl = info.label || AUTOMATED_SWEEP_AGENCY_LABELS[cid] || cid;
          const err = sanitizeActivityText(info.error || "query unavailable");
          return `${lbl} (${err})`;
        });
        const totalAg = Number(activeSweepHealth.totalAgencies || 8);
        const succAg = Number(
          activeSweepHealth.succeededAgencies ?? Math.max(0, totalAg - failedIds.length)
        );
        tryAddSynthetic(
          makeActivityEvent({
            id: evId,
            stream: "watchlist",
            kind: "watchlist_sweep_degraded",
            severity: "warning",
            adverse: false,
            dateIso: sweepDay,
            timestampIso: sweepTs,
            discoveredAt: sweepTs,
            title: `Daily 8-Agency Sweep Degraded (${succAg}/${totalAg} agencies OK)`,
            summary: `${failedIds.length} agency endpoint(s) did not complete cleanly: ${detailParts.join("; ")}. Verify affected checks manually if time-sensitive.`,
            syntheticLive: true,
          })
        );
      }
    }
  }

  // --- Stream 1 & Stream 3: Watchlist properties & checklist findings ---
  const propList = Array.isArray(watchedProperties) ? watchedProperties : [];
  for (const prop of propList) {
    if (!prop || typeof prop !== "object") continue;
    const pid = String(prop.id || "").trim();
    if (!pid) continue;
    const propTitle = String(prop.propertyName || prop.address || pid).trim();
    const propAddr = String(prop.address || "").trim();
    const propHcad = String(prop.hcadAccount || "").trim();
    const isExplicitlyRemoved =
      isRemovedWatchlistTombstone(prop) &&
      (Boolean(prop.removedFromWatchlist) || prop.trackerStatus === REMOVED_WATCHLIST_TOMBSTONE);
    const isArchived =
      prop.trackerStatus === "Archived (Tier 3)" || isRemovedWatchlistTombstone(prop);

    if (isArchived) {
      const archTs = String(prop.archivedAt || prop.updatedAt || "");
      const archDay = toChicagoDateIso(archTs, parseIsoDatePrefix(archTs)) || todayLocal;
      if (passesTemporalGate(archTs, archDay)) {
        tryAddSynthetic(
          makeActivityEvent({
            id: buildStaffPropEventId(pid, isExplicitlyRemoved ? "removed" : "archived"),
            stream: "staff",
            kind: isExplicitlyRemoved
              ? "watchlist_property_removed"
              : "watchlist_property_archived",
            severity: "info",
            adverse: false,
            dateIso: archDay,
            timestampIso: archTs || effectiveNowIso,
            discoveredAt: archTs || effectiveNowIso,
            loggedAt: archTs.includes("T") ? archTs : "",
            title: isExplicitlyRemoved
              ? `Removed from Watchlist: ${propTitle}`
              : `Archived from Active Watchlist: ${propTitle}`,
            summary: isExplicitlyRemoved
              ? `Permanently removed from active tracking (${propAddr || "Houston"}).`
              : `Reclassified to Tier 3 / Archived (${propAddr || "Houston"}).`,
            address: propAddr,
            hcad: propHcad,
            watchPropertyId: prop.trackerStatus === "Archived (Tier 3)" ? pid : "",
            buildingId: prop.buildingId || "",
            lat: prop.lat,
            lng: prop.lng,
            yearBuilt: prop.yearBuilt,
            designation: prop.designation || "",
            syntheticLive: true,
          })
        );
      }
      continue;
    }

    const kd = prop.keyDates && typeof prop.keyDates === "object" ? prop.keyDates : {};
    const startIso =
      parseIsoDatePrefix(kd.monitoringStarted) ||
      parseIsoDatePrefix(prop.createdAt) ||
      parseIsoDatePrefix(prop.updatedAt);
    const addedId = buildStaffPropEventId(pid, "added");
    const createTs = String(prop.createdAt || prop.updatedAt || (startIso ? `${startIso}T12:00:00Z` : ""));
    const canSynthesizeAdd =
      !eventsById.has(addedId) &&
      startIso &&
      startIso >= retentionFloorDate &&
      (hasStaticLog
        ? (!baselineLocalDate || startIso >= baselineLocalDate) && passesTemporalGate(createTs, startIso)
        : startIso >= fallback7dFloorDate);

    if (canSynthesizeAdd) {
      const isStaffAuthored = Boolean(
        !pid.startsWith("plat-") || prop.humanEdited || prop.reportedBy || prop.updatedBy
      );
      tryAddSynthetic(
        makeActivityEvent({
          id: addedId,
          stream: isStaffAuthored ? "staff" : "watchlist",
          kind: "watchlist_property_added",
          severity: String(prop.tier || "1") === "1" ? "warning" : "info",
          adverse: false,
          dateIso: startIso,
          timestampIso: createTs || effectiveNowIso,
          discoveredAt: createTs || effectiveNowIso,
          loggedAt: String(prop.updatedAt || "").includes("T") ? String(prop.updatedAt) : "",
          title: `Added to Property Watchlist (Tier ${prop.tier || "1"}): ${propTitle}`,
          summary: `Tracking ${propAddr || propTitle}${prop.yearBuilt ? ` (Built ${prop.yearBuilt})` : ""}${prop.designation ? ` - ${prop.designation}` : ""}. ${String(prop.notes || "").slice(0, 220)}`.trim(),
          address: propAddr,
          hcad: propHcad,
          watchPropertyId: pid,
          buildingId: prop.buildingId || "",
          lat: prop.lat,
          lng: prop.lng,
          yearBuilt: prop.yearBuilt,
          designation: prop.designation || "",
          links: prop.driveLinks || [],
          syntheticLive: true,
        })
      );
    } else {
      // Property-level dossier metadata / snowball search term / Drive link edit
      const latestPropMutation = [
        prop.updatedAt,
        prop.searchTermsUpdatedAt,
        prop.driveLinksUpdatedAt,
      ]
        .filter((t) => t && String(t).includes("T"))
        .sort((a, b) => (parseIsoEpochMs(b) ?? 0) - (parseIsoEpochMs(a) ?? 0))[0];
      const updDay = latestPropMutation ? toChicagoDateIso(latestPropMutation, todayLocal) : "";
      if (latestPropMutation && updDay && passesTemporalGate(latestPropMutation, updDay)) {
        const byTxt = prop.updatedBy ? ` by ${prop.updatedBy}` : "";
        tryAddSynthetic(
          makeActivityEvent({
            id: buildStaffPropEventId(pid, "edited", updDay),
            stream: "staff",
            kind: "watchlist_property_edited",
            severity: "info",
            adverse: false,
            dateIso: updDay,
            timestampIso: latestPropMutation,
            discoveredAt: latestPropMutation,
            loggedAt: latestPropMutation,
            title: `Watchlist Dossier Updated: ${propTitle}`,
            summary: `Dossier metadata, search terms, or checklist updated${byTxt}.`,
            address: propAddr,
            hcad: propHcad,
            watchPropertyId: pid,
            buildingId: prop.buildingId || "",
            lat: prop.lat,
            lng: prop.lng,
            yearBuilt: prop.yearBuilt,
            designation: prop.designation || "",
            links: prop.driveLinks || [],
            syntheticLive: true,
          })
        );
      }
    }

    // Checklist rows strictly by row.id ("chk-01".."chk-15")
    const checklist = Array.isArray(prop.checklist) ? prop.checklist : [];
    for (const row of checklist) {
      if (!row || typeof row !== "object") continue;
      const rowId = String(row.id || "").trim().toLowerCase();
      if (!rowId) continue;
      const rowActivity = String(row.activity || rowId).trim();
      const checks = Array.isArray(row.checks) ? row.checks : [];

      for (const chk of checks) {
        if (!chk || typeof chk !== "object") continue;
        const resTxt = String(chk.result || "").trim();
        if (!resTxt || isPlatSeedCheck(chk)) continue;
        const chkDate = parseIsoDatePrefix(chk.date) || todayLocal;

        if (isAutoSweepCheck(chk)) {
          const evId = buildAutoCheckEventId(pid, rowId, chk);
          if (eventsById.has(evId)) continue;
          const discRaw = String(
            chk.discoveredAt || row.lastAutoSweepAt || prop.lastAutoSweepAt || ""
          ).trim();
          const discTs = discRaw.includes("T")
            ? discRaw
            : discRaw
            ? `${parseIsoDatePrefix(discRaw)}T12:00:00Z`
            : "";
          const candDay = toChicagoDateIso(discTs || chkDate, chkDate);
          if (!passesTemporalGate(discTs, candDay)) continue;
          const adverse = isAdverseAutoSweepCheck(chk);
          if (!hasStaticLog && !adverse) continue;
          const sev =
            adverse && HIGH_SEVERITY_CHECK_IDS.has(rowId)
              ? "critical"
              : adverse
              ? "warning"
              : "info";
          tryAddSynthetic(
            makeActivityEvent({
              id: evId,
              stream: "watchlist",
              kind: "watchlist_auto_finding",
              severity: sev,
              adverse,
              dateIso: chkDate,
              timestampIso: discTs || `${chkDate}T12:00:00Z`,
              discoveredAt: discTs || `${chkDate}T12:00:00Z`,
              title: `${propTitle}: ${rowActivity}`,
              summary: resTxt.replace(/^\[AUTO-SWEEP\]\s*/i, ""),
              address: propAddr,
              hcad: propHcad,
              watchPropertyId: pid,
              checkId: rowId,
              buildingId: prop.buildingId || "",
              lat: prop.lat,
              lng: prop.lng,
              yearBuilt: prop.yearBuilt,
              designation: prop.designation || "",
              links: chk.links || [],
              syntheticLive: true,
            })
          );
        } else {
          const evId = buildManualCheckEventId(pid, rowId, chk);
          if (eventsById.has(evId)) continue;
          const loggedRaw = String(chk.loggedAt || "").trim();
          // DF-1: Only fall back to row.updatedAt when the check's own substantive date is on/after
          // baselineLocalDate so older sibling checks never inherit a fresh row.updatedAt timestamp.
          let verifiedTs = loggedRaw;
          if (
            !verifiedTs &&
            row.updatedAt &&
            chkDate &&
            (!baselineLocalDate || chkDate >= baselineLocalDate)
          ) {
            verifiedTs = String(row.updatedAt);
          }
          const candDay = loggedRaw
            ? toChicagoDateIso(loggedRaw, chkDate)
            : chkDate;
          if (!passesTemporalGate(verifiedTs, candDay)) continue;
          const tsVal =
            verifiedTs && verifiedTs.includes("T") ? verifiedTs : `${chkDate}T12:00:00Z`;
          tryAddSynthetic(
            makeActivityEvent({
              id: evId,
              stream: "staff",
              kind: "watchlist_manual_check",
              severity: row.status === "Flagged" ? "warning" : "info",
              adverse: false,
              dateIso: chkDate,
              timestampIso: tsVal,
              discoveredAt: tsVal,
              loggedAt: loggedRaw || (verifiedTs.includes("T") ? verifiedTs : ""),
              title: `Staff Check Logged - ${propTitle}: ${rowActivity}`,
              summary: resTxt,
              address: propAddr,
              hcad: propHcad,
              watchPropertyId: pid,
              checkId: rowId,
              buildingId: prop.buildingId || "",
              lat: prop.lat,
              lng: prop.lng,
              yearBuilt: prop.yearBuilt,
              designation: prop.designation || "",
              links: chk.links || [],
              syntheticLive: true,
            })
          );
        }
      }

      const statusTs =
        typeof row.statusChangedAt === "string"
          ? row.statusChangedAt.trim()
          : String(row.updatedAt || "").trim();
      if (row.humanStatusEdited && row.status === "Flagged" && statusTs) {
        const statusDay = toChicagoDateIso(statusTs, parseIsoDatePrefix(row.lastChecked) || todayLocal);
        const stId = buildWatchStatusEventId(pid, rowId, row.status, statusDay);
        if (!eventsById.has(stId) && passesTemporalGate(statusTs, statusDay)) {
          tryAddSynthetic(
            makeActivityEvent({
              id: stId,
              stream: "staff",
              kind: "watchlist_status_escalation",
              severity: HIGH_SEVERITY_CHECK_IDS.has(rowId) ? "critical" : "warning",
              adverse: true,
              dateIso: statusDay,
              timestampIso: statusTs,
              discoveredAt: statusTs,
              loggedAt: statusTs,
              title: `${propTitle}: ${rowActivity} Escalated to Flagged`,
              summary: `Checklist status escalated to Flagged on ${propAddr || propTitle}.`,
              address: propAddr,
              hcad: propHcad,
              watchPropertyId: pid,
              checkId: rowId,
              buildingId: prop.buildingId || "",
              lat: prop.lat,
              lng: prop.lng,
              yearBuilt: prop.yearBuilt,
              designation: prop.designation || "",
              syntheticLive: true,
            })
          );
        }
      }
    }
  }

  // --- Stream 2 & Stream 3: Plat Agenda Cycles & Staff Plat Reviews ---
  const cyclesObj =
    platState && typeof platState === "object"
      ? platState.cycles && typeof platState.cycles === "object"
        ? platState.cycles
        : platState.cycleKey && Array.isArray(platState.items)
        ? { [platState.cycleKey]: platState }
        : {}
      : {};
  const platGenIso = String(platState?.generatedAt || "").trim();

  for (const cycleKey of Object.keys(cyclesObj).sort()) {
    const cyc = cyclesObj[cycleKey];
    if (!cyc || typeof cyc !== "object") continue;
    const cycGenIso = String(cyc.generatedAt || platGenIso || "").trim();
    const items = Array.isArray(cyc.items) ? cyc.items : [];
    const reviews = cyc.reviews && typeof cyc.reviews === "object" ? cyc.reviews : {};
    const itemsByApp = new Map();
    for (const it of items) {
      if (it && it.app) itemsByApp.set(String(it.app).trim(), it);
    }

    const cycEventDay = cycleKey > todayLocal && todayLocal ? todayLocal : cycleKey;
    const cycId = buildAgendaCycleEventId(cycleKey);
    if (
      !eventsById.has(cycId) &&
      (!baselineLocalDate || cycleKey >= baselineLocalDate) &&
      passesTemporalGate(cycGenIso, cycEventDay)
    ) {
      const summ = cyc.summary || {};
      const tot = summ.totalAgendaItems ?? items.length;
      const toChk = summ.toCheckCount ?? items.length;
      const dpvCnt = summ.dpvVarianceCount ?? 0;
      const phCnt = summ.publicHearingNoticeCount ?? 0;
      const cLinks = [];
      if (cyc.sourceSpreadsheetUrl) {
        cLinks.push({ label: "Agenda Spreadsheet (.xlsx)", url: cyc.sourceSpreadsheetUrl });
      }
      if (cyc.agendaOutlineUrl) {
        cLinks.push({ label: "PC Draft Agenda Outline (PDF)", url: cyc.agendaOutlineUrl });
      }
      tryAddSynthetic(
        makeActivityEvent({
          id: cycId,
          stream: "agenda",
          kind: "agenda_cycle_added",
          severity: dpvCnt || toChk ? "warning" : "info",
          adverse: false,
          dateIso: cycleKey,
          timestampIso: cycGenIso || `${cycEventDay}T12:00:00Z`,
          discoveredAt: cycGenIso || `${cycEventDay}T12:00:00Z`,
          title: `Planning Commission Agenda Posted: ${cycleKey}`,
          summary: `Ingested ${tot} agenda items (${toChk} flagged for historic review, ${dpvCnt} Section K Development Plat Variance(s), ${phCnt} Public Hearing Notice(s)).`,
          cycleKey,
          links: cLinks,
          syntheticLive: true,
        })
      );

      for (const it of items) {
        if (!it || !it.app) continue;
        if (!isNotableNewCycleItem(it, reviews)) continue;
        const app = String(it.app).trim();
        const section = String(it.section || "");
        const typ = String(it.type || "");
        const isDpv =
          app.toUpperCase().startsWith("DPV") ||
          section.includes("K.") ||
          typ.toUpperCase().includes("DPV");
        const atlas = it.atlas && typeof it.atlas === "object" ? it.atlas : {};
        const yr = Number(atlas.earliestYearBuilt || 0);
        const kindTok = isDpv
          ? "dpv"
          : it.noticePdf && !it.pdf
          ? "notice_pdf"
          : "added";
        const isCrit = Boolean(
          atlas.hasActivePermit ||
            (Array.isArray(atlas.designations) && atlas.designations.length > 0) ||
            (yr >= 1836 && yr <= 1940 && isDpv)
        );
        const iLinks = [];
        if (it.pdf) iLinks.push({ label: "Plat PDF", url: it.pdf });
        if (it.noticePdf) iLinks.push({ label: "Public Hearing Notice PDF", url: it.noticePdf });
        tryAddSynthetic(
          makeActivityEvent({
            id: buildAgendaItemEventId(cycleKey, app, kindTok),
            stream: "agenda",
            kind: `agenda_item_${kindTok}`,
            severity: isCrit
              ? "critical"
              : isDpv || it.noticePdf || (yr >= 1836 && yr <= 1940)
              ? "warning"
              : "info",
            adverse: isCrit,
            dateIso: cycEventDay,
            timestampIso: cycGenIso || `${cycEventDay}T12:00:00Z`,
            discoveredAt: cycGenIso || `${cycEventDay}T12:00:00Z`,
            title: isDpv
              ? `Section K DPV Filed (${cycleKey}): ${it.name || app}`
              : kindTok === "notice_pdf"
              ? `Public Hearing Notice Posted (${cycleKey}): ${it.name || app}`
              : `Notable Plat Filing (${cycleKey}): ${it.name || app} (${app})`,
            summary: `${typ || "Plat"} in ${section || "Agenda"}${yr >= 1836 ? ` | Built ${yr}` : ""}${it.hearingDateIso ? ` | Hearing ${it.hearingDateIso}` : ""}${it.varianceSummary ? ` | ${it.varianceSummary}` : ""}.`,
            address:
              atlas.primaryAddress ||
              it.noticeLocation ||
              (Array.isArray(atlas.addresses) ? atlas.addresses[0] : "") ||
              "",
            hcad: it.hcad || "",
            cycleKey,
            appNo: app,
            occKey: it.occurrenceKey || `${cycleKey}::${app}`,
            buildingId: atlas.buildingId || "",
            lat: atlas.lat,
            lng: atlas.lng,
            yearBuilt: atlas.earliestYearBuilt,
            designation: (atlas.designations || []).join(", ") || it.hd || "",
            links: iLinks,
            syntheticLive: true,
          })
        );
      }
    }

    // Confirmed Staff Plat Reviews (Stream 3)
    for (const [appNo, rev] of Object.entries(reviews)) {
      if (!rev || typeof rev !== "object" || !rev.tierConfirmed) continue;
      const tierVal = String(rev.tier || "").trim();
      if (!tierVal) continue;
      const evId = buildStaffReviewEventId(cycleKey, appNo, tierVal);
      if (eventsById.has(evId)) continue;
      const updRaw = String(rev.updated || "").trim();
      const revDay = toChicagoDateIso(updRaw, cycleKey) || cycleKey;
      if (!passesTemporalGate(updRaw, revDay)) continue;
      const matchedIt = itemsByApp.get(String(appNo).trim()) || { app: appNo, name: appNo };
      const atlas = matchedIt.atlas && typeof matchedIt.atlas === "object" ? matchedIt.atlas : {};
      const rLinks = [];
      if (matchedIt.pdf) rLinks.push({ label: "Plat PDF", url: matchedIt.pdf });
      if (matchedIt.noticePdf) {
        rLinks.push({ label: "Public Hearing Notice PDF", url: matchedIt.noticePdf });
      }
      const tsVal = updRaw && updRaw.includes("T") ? updRaw : `${revDay}T12:00:00Z`;
      tryAddSynthetic(
        makeActivityEvent({
          id: evId,
          stream: "staff",
          kind: "staff_plat_review",
          severity: tierVal === "1" ? "warning" : "info",
          adverse: false,
          dateIso: revDay,
          timestampIso: tsVal,
          discoveredAt: tsVal,
          loggedAt: updRaw && updRaw.includes("T") ? updRaw : "",
          title: `Plat Review Confirmed (Tier ${tierVal}): ${matchedIt.name || appNo} (${appNo})`,
          summary: `Confirmed Tier ${tierVal}${rev.reviewer ? ` by ${rev.reviewer}` : ""}${rev.threatCategory ? ` (${rev.threatCategory})` : ""}${rev.why ? `: ${rev.why}` : ""}.`,
          address:
            atlas.primaryAddress ||
            matchedIt.noticeLocation ||
            (Array.isArray(atlas.addresses) ? atlas.addresses[0] : "") ||
            "",
          hcad: matchedIt.hcad || "",
          cycleKey,
          appNo,
          occKey: matchedIt.occurrenceKey || `${cycleKey}::${appNo}`,
          buildingId: atlas.buildingId || "",
          lat: atlas.lat,
          lng: atlas.lng,
          yearBuilt: atlas.earliestYearBuilt,
          designation: (atlas.designations || []).join(", ") || matchedIt.hd || "",
          links: rLinks,
          syntheticLive: true,
        })
      );
    }
  }

  // --- Stream 4A: Citywide Historic Permit & DSHS Asbestos Alerts ---
  if (permitAlertsPayload && typeof permitAlertsPayload === "object") {
    const pGenAt = String(
      permitAlertsPayload.generated_at || permitAlertsPayload.generatedAt || ""
    ).trim();
    const shouldScanPermits = !hasStaticLog || !ledgerBaselineIso || isIsoStrictlyAfter(pGenAt, ledgerBaselineIso);
    if (shouldScanPermits) {
      const alerts = Array.isArray(permitAlertsPayload.alerts) ? permitAlertsPayload.alerts : [];
      for (const al of alerts) {
        if (!al || typeof al !== "object") continue;
        const hcadRaw = al.hcad_num || "";
        const titleName = sanitizeActivityText(
          al.title || al.building_name || al.address || "Historic Structure"
        );
        const addr = sanitizeActivityText(al.address || "");
        const desigParts = [
          al.landmark_name ? `City Landmark: ${al.landmark_name}` : "",
          al.historic_district ? `Historic District: ${al.historic_district}` : "",
          String(al.good_brick_summary || "").trim(),
        ].filter(Boolean);
        const desigStr = desigParts.join(" | ");
        const lngVal = al.lng ?? al.lon ?? null;

        for (const cp of al.coh_permits || []) {
          if (!cp || typeof cp !== "object") continue;
          const pDate =
            parseIsoDatePrefix(cp.date) || parseIsoDatePrefix(al.latest_filing_date) || todayLocal;
          const evId = buildPermitCohEventId(hcadRaw, cp.project_no, cp.use_desc || "", pDate);
          if (eventsById.has(evId)) continue;
          if (hasStaticLog && baselineLocalDate && pDate < baselineLocalDate && !cp.discovered_at) {
            continue;
          }
          if (!passesTemporalGate(cp.discovered_at || pGenAt, pDate)) continue;
          const isCrit = Boolean(
            cp.is_structural_demo ||
              cp.is_sewer_disconnect ||
              String(al.severity || "").toLowerCase() === "critical"
          );
          const pno = String(cp.project_no || "N/A").trim();
          const lbl = sanitizeActivityText(cp.permit_type_label || cp.permit_desc || "City Permit");
          const useD = sanitizeActivityText(cp.use_desc || "");
          const buyer = sanitizeActivityText(cp.buyer || "");
          tryAddSynthetic(
            makeActivityEvent({
              id: evId,
              stream: "permits",
              kind: "permit_coh_alert",
              severity: isCrit ? "critical" : "warning",
              adverse: true,
              dateIso: pDate,
              timestampIso: pGenAt || `${pDate}T12:00:00Z`,
              discoveredAt: pGenAt || `${pDate}T12:00:00Z`,
              title: `${lbl} (#${pno}): ${titleName}`,
              summary: `${addr}${al.year_built ? ` (Built ${al.year_built})` : ""}: ${useD || lbl} issued ${pDate}${buyer ? ` to ${buyer}` : ""}${desigStr ? ` | ${desigStr}` : ""}.`,
              address: addr,
              hcad: String(hcadRaw),
              watchPropertyId: al.watchlist_id || "",
              buildingId: al.building_id || "",
              lat: al.lat,
              lng: lngVal,
              yearBuilt: al.year_built,
              designation: desigStr,
              links: [
                {
                  label: "Houston Sold Permits",
                  url: "https://cohtora.houstontx.gov/approot/soldpermits/online_permit.htm",
                },
              ],
              permitMeta: buildPermitAlertMeta(al, "coh", cp),
              syntheticLive: true,
            })
          );
        }

        for (const dn of al.dshs_notifications || []) {
          if (!dn || typeof dn !== "object") continue;
          const dDate =
            parseIsoDatePrefix(dn.filing_date) ||
            parseIsoDatePrefix(dn.start_date) ||
            parseIsoDatePrefix(al.latest_filing_date) ||
            todayLocal;
          const evId = buildPermitDshsEventId(
            hcadRaw,
            dn.notification_number,
            dn.norm_address || addr,
            dDate
          );
          if (eventsById.has(evId)) continue;
          if (hasStaticLog && baselineLocalDate && dDate < baselineLocalDate && !dn.discovered_at) {
            continue;
          }
          if (!passesTemporalGate(dn.discovered_at || pGenAt, dDate)) continue;
          const nno = String(dn.notification_number || "N/A").trim();
          const wtype = sanitizeActivityText(dn.work_type || "Asbestos/Demolition");
          const windowTxt =
            dn.start_date && dn.end_date ? `scheduled ${dn.start_date} to ${dn.end_date}` : "";
          const dLinks = [];
          if (dn.print_action_url) dLinks.push({ label: `DSHS #${nno}`, url: dn.print_action_url });
          if (dn.pdf_url) dLinks.push({ label: "DSHS Record", url: dn.pdf_url });
          tryAddSynthetic(
            makeActivityEvent({
              id: evId,
              stream: "permits",
              kind: "permit_dshs_alert",
              severity: "critical",
              adverse: true,
              dateIso: dDate,
              timestampIso: pGenAt || `${dDate}T12:00:00Z`,
              discoveredAt: pGenAt || `${dDate}T12:00:00Z`,
              title: `TX DSHS ${wtype} Filing (#${nno}): ${titleName}`,
              summary: `${addr}${al.year_built ? ` (Built ${al.year_built})` : ""}: DSHS Notification #${nno} filed ${dDate}${windowTxt ? ` (${windowTxt})` : ""}${dn.abatement_contractor ? ` by ${dn.abatement_contractor}` : ""}${desigStr ? ` | ${desigStr}` : ""}.`,
              address: addr,
              hcad: String(hcadRaw),
              watchPropertyId: al.watchlist_id || "",
              buildingId: al.building_id || "",
              lat: al.lat,
              lng: lngVal,
              yearBuilt: al.year_built,
              designation: desigStr,
              links: dLinks,
              permitMeta: buildPermitAlertMeta(al, "dshs", dn),
              syntheticLive: true,
            })
          );
        }
      }
    }
  }

  // --- Stream 4B: HAHC COA Monthly Docket, Rulings & Guideline Redlines ---
  if (hahcPayload && typeof hahcPayload === "object") {
    const hGenAt = String(hahcPayload.generated_at || hahcPayload.generatedAt || "").trim();
    const shouldScanHahc = !hasStaticLog || !ledgerBaselineIso || isIsoStrictlyAfter(hGenAt, ledgerBaselineIso);
    if (shouldScanHahc) {
      const cc =
        hahcPayload.current_cycle && typeof hahcPayload.current_cycle === "object"
          ? hahcPayload.current_cycle
          : {};
      const mDate = parseIsoDatePrefix(cc.meeting_date);
      const mEventDay = mDate && todayLocal && mDate > todayLocal ? todayLocal : mDate;

      if (
        mDate &&
        (!hasStaticLog || !baselineLocalDate || mDate >= baselineLocalDate) &&
        passesTemporalGate(hGenAt, mEventDay)
      ) {
        const cId = buildHahcCycleEventId(mDate);
        const cLinks = [];
        if (cc.agenda_pdf_url) cLinks.push({ label: "HAHC Agenda PDF", url: cc.agenda_pdf_url });
        if (hahcPayload.source_url) {
          cLinks.push({ label: "COH HAHC Portal", url: hahcPayload.source_url });
        }
        tryAddSynthetic(
          makeActivityEvent({
            id: cId,
            stream: "permits",
            kind: "hahc_cycle_posted",
            severity: "info",
            adverse: false,
            dateIso: mDate,
            timestampIso: hGenAt || `${mEventDay}T12:00:00Z`,
            discoveredAt: hGenAt || `${mEventDay}T12:00:00Z`,
            title: `HAHC Monthly Agenda Cycle Posted: ${mDate}`,
            summary: `${cc.active_items_count || 0} Certificate of Appropriateness (COA) item(s) scheduled on the ${mDate} Houston Archaeological & Historical Commission docket.`,
            links: cLinks,
            syntheticLive: true,
          })
        );

        for (const g of cc.guideline_updates || []) {
          if (!g || typeof g !== "object") continue;
          const gTitle = sanitizeActivityText(g.title || "");
          const gUrl = String(g.pdf_url || "").trim();
          if (!gTitle && !gUrl) continue;
          const gId = buildHahcGuidelineEventId(mDate, gTitle || gUrl);
          tryAddSynthetic(
            makeActivityEvent({
              id: gId,
              stream: "permits",
              kind: "hahc_guideline_update",
              severity: "warning",
              adverse: false,
              dateIso: mDate,
              timestampIso: hGenAt || `${mEventDay}T12:00:00Z`,
              discoveredAt: hGenAt || `${mEventDay}T12:00:00Z`,
              title: `HAHC Historic District Design Guideline Draft: ${gTitle || "Redline Draft"}`,
              summary: `Design guideline redline/update posted with the ${mDate} HAHC packet: ${gTitle || gUrl}.`,
              links: gUrl ? [{ label: gTitle || "Guideline Draft PDF", url: gUrl }] : [],
              syntheticLive: true,
            })
          );
        }
      }

      // Track static filing prefixes so an in-place ruling on a recent active filing can transition cleanly
      const staticFilingPrefixSet = new Set();
      if (hasStaticLog) {
        for (const evId of eventsById.keys()) {
          const m = evId.match(/^(hahc:filing:[^:]+):/);
          if (m) staticFilingPrefixSet.add(m[1]);
        }
      }

      for (const af of hahcPayload.filings || []) {
        if (!af || typeof af !== "object") continue;
        const fid = String(af.filing_id || "").trim();
        const fDate = parseIsoDatePrefix(af.meeting_date);
        if (!fid || !fDate) continue;
        if (fDate < retentionFloorDate) continue;
        const outcomeRaw = String(af.outcome || "pending_agenda").trim();
        const evId = buildHahcFilingEventId(fid, outcomeRaw);
        if (eventsById.has(evId)) continue;

        const prefix = `hahc:filing:${fid}`;
        const isKnownRulingTransition = staticFilingPrefixSet.has(prefix);
        const fEventDay = todayLocal && fDate > todayLocal ? todayLocal : fDate;
        const filingMutTs = String(af.updated_at || hGenAt || "").trim();

        // DF-1: Strictly bar pre-baseline HAHC archive rows from resurrecting unless they are an
        // in-place outcome transition on an already-tracked 90-day filing or carry `updated_at > baseline`.
        if (hasStaticLog && baselineLocalDate && fDate < baselineLocalDate) {
          if (!isKnownRulingTransition && !isIsoStrictlyAfter(af.updated_at || "", ledgerBaselineIso)) {
            continue;
          }
        }
        if (!passesTemporalGate(filingMutTs, isKnownRulingTransition ? todayLocal : fEventDay)) {
          continue;
        }

        const appCat = String(af.app_category || "").trim().toLowerCase();
        const appType = sanitizeActivityText(af.app_type || "COA Application");
        const addr = sanitizeActivityText(af.address || "");
        const desig = [
          af.historic_district ? `Historic District: ${sanitizeActivityText(af.historic_district)}` : "",
          af.landmark_name ? `Landmark: ${sanitizeActivityText(af.landmark_name)}` : "",
        ]
          .filter(Boolean)
          .join(" | ");
        const outcomeLbl = formatHahcOutcomeLabel(outcomeRaw);
        const outSlug = slugToken(outcomeRaw);
        const isDemoReloc =
          appCat === "demolition_relocation" || /demolition|relocation/i.test(appType);
        const adverse = Boolean(
          isDemoReloc &&
            (outSlug === "pending-agenda" ||
              outSlug === "approved" ||
              outSlug === "approved-conditions")
        );
        const sev = adverse
          ? "critical"
          : isDemoReloc ||
            outSlug.includes("denied") ||
            outSlug.includes("defer") ||
            appCat === "designation"
          ? "warning"
          : "info";
        const fLinks = [];
        if (af.agenda_or_minutes_url) {
          fLinks.push({ label: "HAHC Agenda / Action Minutes PDF", url: af.agenda_or_minutes_url });
        }
        if (af.staff_report_url) {
          fLinks.push({ label: "HAHC Staff Report PDF", url: af.staff_report_url });
        }
        const detail = sanitizeActivityText(af.outcome_detail || "");
        const effDay = isKnownRulingTransition ? todayLocal : fEventDay;
        tryAddSynthetic(
          makeActivityEvent({
            id: evId,
            stream: "permits",
            kind: isKnownRulingTransition ? "hahc_filing_outcome" : "hahc_filing_added",
            severity: sev,
            adverse,
            dateIso: fDate,
            timestampIso: filingMutTs || `${effDay}T12:00:00Z`,
            discoveredAt: filingMutTs || `${effDay}T12:00:00Z`,
            title: isKnownRulingTransition
              ? `HAHC Ruling Updated (${outcomeLbl}): ${addr || fid} (${appType})`
              : `HAHC COA Docket (${outcomeLbl}): ${addr || fid} (${appType})`,
            summary: `HAHC Meeting ${fDate}${af.item_num ? ` Item #${af.item_num}` : ""}: ${appType} at ${addr || "Houston"}${desig ? ` (${desig})` : ""} - ${outcomeLbl}${detail && detail.toLowerCase() !== outcomeLbl.toLowerCase() ? ` (${detail.slice(0, 180)})` : ""}.`,
            address: addr,
            hcad: af.hcad_num || "",
            lat: af.lat,
            lng: af.lng,
            yearBuilt: af.year_built,
            designation: desig,
            links: fLinks,
            syntheticLive: true,
          })
        );
      }
    }
  }

  const events = sortEventsLedgerOrder(Array.from(eventsById.values()));

  // Build merged Daily Run Ledger rows (recomputing per-day counters idempotently from merged events)
  const runsByDate = new Map();
  for (const prevRun of staticLog?.dailyRuns || []) {
    if (!prevRun || typeof prevRun !== "object") continue;
    const rd = parseIsoDatePrefix(prevRun.date);
    if (!rd || rd < retentionFloorDate || (todayLocal && rd > todayLocal)) continue;
    runsByDate.set(rd, {
      ...prevRun,
      failedAgencies: Array.isArray(prevRun.failedAgencies) ? prevRun.failedAgencies.slice() : [],
    });
  }

  const totalAgencies = Number(activeSweepHealth?.totalAgencies || 8);
  const failedToday = Array.isArray(activeSweepHealth?.failedAgencies)
    ? activeSweepHealth.failedAgencies.slice()
    : [];
  const succeededToday = Number(
    activeSweepHealth?.succeededAgencies ?? Math.max(0, totalAgencies - failedToday.length)
  );
  const sweepDayIso = activeSweepHealth?.timestamp
    ? toChicagoDateIso(activeSweepHealth.timestamp, todayLocal)
    : "";

  if (sweepDayIso && sweepDayIso >= retentionFloorDate && (!todayLocal || sweepDayIso <= todayLocal)) {
    const existingSweepDay = runsByDate.get(sweepDayIso) || {};
    runsByDate.set(sweepDayIso, {
      date: sweepDayIso,
      lastRunAt: activeSweepHealth?.timestamp || existingSweepDay.lastRunAt || effectiveNowIso,
      runCount: Number(existingSweepDay.runCount || 1),
      sweepsExecuted: activeSweepHealth?.sweepsExecuted !== false,
      propertiesSwept: Number(
        activeSweepHealth?.propertiesSwept ?? existingSweepDay.propertiesSwept ?? propList.length
      ),
      totalAgencies,
      succeededAgencies: succeededToday,
      failedAgencies: failedToday,
      allOk: failedToday.length === 0,
      eventCount: 0,
      adverseEventCount: 0,
      byStream: { watchlist: 0, agenda: 0, staff: 0, permits: 0 },
    });
  }

  for (const ev of events) {
    if (!runsByDate.has(ev.localDate)) {
      runsByDate.set(ev.localDate, {
        date: ev.localDate,
        lastRunAt: getEventEffectiveTimestamp(ev) || `${ev.localDate}T12:00:00Z`,
        runCount: ev.localDate === todayLocal && !hasStaticLog ? 0 : 1,
        sweepsExecuted: ev.localDate !== todayLocal || sweepDayIso === todayLocal,
        propertiesSwept: propList.length,
        totalAgencies,
        succeededAgencies: totalAgencies,
        failedAgencies: [],
        allOk: true,
        liveOnlyDay: ev.localDate !== sweepDayIso,
        eventCount: 0,
        adverseEventCount: 0,
        byStream: { watchlist: 0, agenda: 0, staff: 0, permits: 0 },
      });
    }
  }

  const eventsByDay = new Map();
  for (const ev of events) {
    const arr = eventsByDay.get(ev.localDate) || [];
    arr.push(ev);
    eventsByDay.set(ev.localDate, arr);
  }

  for (const [dIso, runRec] of runsByDate.entries()) {
    const dayEvs = (eventsByDay.get(dIso) || []).filter(
      (e) => e.kind !== "watchlist_sweep_degraded"
    );
    runRec.eventCount = dayEvs.length;
    runRec.adverseEventCount = dayEvs.filter(
      (e) => e.adverse || e.severity === "critical"
    ).length;
    runRec.byStream = {
      watchlist: dayEvs.filter((e) => e.stream === "watchlist").length,
      agenda: dayEvs.filter((e) => e.stream === "agenda").length,
      staff: dayEvs.filter((e) => e.stream === "staff").length,
      permits: dayEvs.filter((e) => e.stream === "permits").length,
    };
  }

  const dailyRuns = Array.from(runsByDate.values()).sort((a, b) =>
    String(b.date || "").localeCompare(String(a.date || ""))
  );

  return {
    hasStaticLog,
    ledgerBaselineIso,
    retentionFloorDate,
    todayLocal,
    lastSweepHealth: activeSweepHealth,
    events,
    dailyRuns,
  };
}

// ---------------------------------------------------------------------------
// 4. Read Watermark, Filtering, Quiet-Day Compaction & Entity Unread Index
// ---------------------------------------------------------------------------

export function computeWatermarkState({
  events = [],
  caughtUpAt = "",
  readEventIds = [],
  nowIso = "",
} = {}) {
  const effectiveNowIso = String(nowIso || "").trim() || new Date().toISOString();
  const todayLocal = toChicagoDateIso(effectiveNowIso);
  const implicitFloorDate = shiftIsoDate(todayLocal, -6);
  const readIdSet = new Set(
    (Array.isArray(readEventIds) ? readEventIds : [])
      .map((x) => String(x || "").trim())
      .filter(Boolean)
  );

  const wmMs = parseIsoEpochMs(caughtUpAt);
  const isImplicitFirstVisit = wmMs === null;
  const unreadIds = new Set();
  let unreadAdverseCount = 0;

  for (const ev of Array.isArray(events) ? events : []) {
    if (!ev || !ev.id || ev.kind === "watchlist_sweep_degraded") continue;
    const id = String(ev.id);
    if (readIdSet.has(id)) continue;

    let isUnread = false;
    if (isImplicitFirstVisit) {
      const ld = parseIsoDatePrefix(ev.localDate) || parseIsoDatePrefix(ev.date);
      isUnread = Boolean(ld && ld >= implicitFloorDate);
    } else {
      const evMs = parseIsoEpochMs(getEventEffectiveTimestamp(ev));
      isUnread = evMs !== null && evMs > wmMs;
    }

    if (isUnread) {
      unreadIds.add(id);
      if (ev.adverse || ev.severity === "critical") {
        unreadAdverseCount += 1;
      }
    }
  }

  return {
    isImplicitFirstVisit,
    caughtUpAt: isImplicitFirstVisit ? "" : String(caughtUpAt).trim(),
    implicitFloorDate,
    unreadIds,
    unreadCount: unreadIds.size,
    unreadAdverseCount,
  };
}

export function buildMarkCaughtUpState({
  events = [],
  prevReadEventIds = [],
  nowIso = "",
  maxIds = MAX_STORED_READ_EVENT_IDS,
} = {}) {
  const effectiveNowIso = String(nowIso || "").trim() || new Date().toISOString();
  let maxMs = parseIsoEpochMs(effectiveNowIso) ?? Date.now();

  const sortedNewest = sortEventsLedgerOrder(Array.isArray(events) ? events : []);
  for (const ev of sortedNewest) {
    const evMs = parseIsoEpochMs(getEventEffectiveTimestamp(ev));
    if (evMs !== null && evMs > maxMs) {
      maxMs = evMs;
    }
  }

  const limit = Math.max(1, Number(maxIds) || MAX_STORED_READ_EVENT_IDS);
  const seen = new Set();
  const readEventIds = [];

  for (const ev of sortedNewest) {
    if (!ev?.id) continue;
    const id = String(ev.id).trim();
    if (id && !seen.has(id)) {
      seen.add(id);
      readEventIds.push(id);
      if (readEventIds.length >= limit) break;
    }
  }

  if (readEventIds.length < limit && Array.isArray(prevReadEventIds)) {
    // Append newest prior IDs to fill remaining capacity while evicting oldest entries first
    for (let i = prevReadEventIds.length - 1; i >= 0 && readEventIds.length < limit; i -= 1) {
      const id = String(prevReadEventIds[i] || "").trim();
      if (id && !seen.has(id)) {
        seen.add(id);
        readEventIds.push(id);
      }
    }
  }

  return {
    caughtUpAt: new Date(maxMs).toISOString(),
    readEventIds,
  };
}

export function sortEventsForBriefing(events = []) {
  return (Array.isArray(events) ? events : []).slice().sort((a, b) => {
    const sevA = SEVERITY_RANK[a.severity] ?? 2;
    const sevB = SEVERITY_RANK[b.severity] ?? 2;
    if (sevA !== sevB) return sevA - sevB;
    const advA = a.adverse ? 0 : 1;
    const advB = b.adverse ? 0 : 1;
    if (advA !== advB) return advA - advB;
    const ldCmp = String(b.localDate || "").localeCompare(String(a.localDate || ""));
    if (ldCmp !== 0) return ldCmp;
    const msA = parseIsoEpochMs(getEventEffectiveTimestamp(a)) ?? 0;
    const msB = parseIsoEpochMs(getEventEffectiveTimestamp(b)) ?? 0;
    if (msA !== msB) return msB - msA;
    return String(a.id || "").localeCompare(String(b.id || ""));
  });
}

export function filterActivityEvents({
  events = [],
  watermarkState = null,
  rangeMode = "since_last_visit",
  selectedDate = "",
  streamFilter = "all",
  query = "",
  nowIso = "",
} = {}) {
  const effectiveNowIso = String(nowIso || "").trim() || new Date().toISOString();
  const todayLocal = toChicagoDateIso(effectiveNowIso);
  const floor7d = shiftIsoDate(todayLocal, -6);
  const cleanDate = parseIsoDatePrefix(selectedDate);
  const cleanStream = String(streamFilter || "all").trim().toLowerCase();
  const qNorm = String(query || "").trim().toLowerCase();
  const wm =
    watermarkState || computeWatermarkState({ events, caughtUpAt: "", readEventIds: [], nowIso: effectiveNowIso });

  const matched = [];
  for (const ev of Array.isArray(events) ? events : []) {
    if (!ev || typeof ev !== "object" || ev.kind === "watchlist_sweep_degraded") continue;

    if (cleanDate) {
      if (ev.localDate !== cleanDate) continue;
    } else if (rangeMode === "since_last_visit") {
      if (!wm.unreadIds.has(String(ev.id))) continue;
    } else if (rangeMode === "7d") {
      if (!ev.localDate || ev.localDate < floor7d) continue;
    }

    if (cleanStream && cleanStream !== "all" && ev.stream !== cleanStream) {
      continue;
    }

    if (qNorm) {
      const hay = [
        ev.title,
        ev.summary,
        ev.address,
        ev.hcad,
        ev.appNo,
        ev.designation,
        ev.watchPropertyId,
        ev.checkId,
        ev.cycleKey,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      if (!hay.includes(qNorm)) continue;
    }

    matched.push(ev);
  }

  return sortEventsForBriefing(matched);
}

export function groupActivityEventsByDate(events = []) {
  const map = new Map();
  for (const ev of Array.isArray(events) ? events : []) {
    const key = parseIsoDatePrefix(ev?.localDate) || parseIsoDatePrefix(ev?.date) || "Unknown";
    const list = map.get(key) || [];
    list.push(ev);
    map.set(key, list);
  }
  return Array.from(map.entries())
    .sort((a, b) => b[0].localeCompare(a[0]))
    .map(([date, list]) => ({
      date,
      events: sortEventsForBriefing(list),
    }));
}

/**
 * Compact contiguous zero-event healthy days in the Left Pane Daily Run Ledger (`DF-4`, `UT-6`).
 * CRITICAL INVARIANT: Any day with `failedAgencies.length > 0` or `allOk === false` is NEVER
 * collapsed into a quiet-day summary row so staff immediately spot degraded agency sweeps.
 */
export function compactDailyRunsForLedger(dailyRuns = [], { expandedRangeKeys = [] } = {}) {
  const expandedSet = new Set(Array.isArray(expandedRangeKeys) ? expandedRangeKeys : []);
  const ordered = (Array.isArray(dailyRuns) ? dailyRuns : [])
    .filter((r) => r && r.date)
    .slice()
    .sort((a, b) => String(b.date).localeCompare(String(a.date)));

  const items = [];
  let quietStreak = [];

  const flushStreak = () => {
    if (quietStreak.length === 0) return;
    if (quietStreak.length === 1) {
      items.push({ type: "day", run: quietStreak[0] });
    } else {
      const newestDate = quietStreak[0].date;
      const oldestDate = quietStreak[quietStreak.length - 1].date;
      const rangeKey = `${newestDate}..${oldestDate}`;
      const isExpanded = expandedSet.has(rangeKey);
      items.push({
        type: "quiet_range",
        rangeKey,
        startDate: oldestDate,
        endDate: newestDate,
        count: quietStreak.length,
        expanded: isExpanded,
        runs: quietStreak.slice(),
      });
      if (isExpanded) {
        for (const r of quietStreak) {
          items.push({ type: "day", run: r, parentRangeKey: rangeKey });
        }
      }
    }
    quietStreak = [];
  };

  for (const run of ordered) {
    const hasFailedAgencies = Array.isArray(run.failedAgencies) && run.failedAgencies.length > 0;
    const isDegraded = hasFailedAgencies || run.allOk === false;
    const isQuietHealthy = Number(run.eventCount || 0) === 0 && !isDegraded;

    if (isQuietHealthy) {
      quietStreak.push(run);
    } else {
      flushStreak();
      items.push({ type: "day", run });
    }
  }
  flushStreak();

  return items;
}

export function buildEntityUnreadIndex(events = [], watermarkState = null) {
  const unreadIds = watermarkState?.unreadIds instanceof Set ? watermarkState.unreadIds : new Set();
  const watchPropUnread = new Map();
  const watchCheckUnread = new Set();
  const agendaOccUnread = new Map();
  const agendaAppUnread = new Map();

  for (const ev of Array.isArray(events) ? events : []) {
    if (!ev || !unreadIds.has(String(ev.id))) continue;
    const isCrit = ev.severity === "critical" || Boolean(ev.adverse);

    if (ev.watchPropertyId) {
      const prev = watchPropUnread.get(ev.watchPropertyId) || {
        count: 0,
        hasCritical: false,
        checkIds: new Set(),
      };
      prev.count += 1;
      if (isCrit) prev.hasCritical = true;
      if (ev.checkId) {
        prev.checkIds.add(ev.checkId);
        watchCheckUnread.add(`${ev.watchPropertyId}::${ev.checkId}`);
      }
      watchPropUnread.set(ev.watchPropertyId, prev);
    }

    if (ev.occKey) {
      const prevOcc = agendaOccUnread.get(ev.occKey) || { count: 0, hasCritical: false };
      prevOcc.count += 1;
      if (isCrit) prevOcc.hasCritical = true;
      agendaOccUnread.set(ev.occKey, prevOcc);
    }

    if (ev.cycleKey && ev.appNo) {
      const key = `${ev.cycleKey}::${ev.appNo}`;
      const prevApp = agendaAppUnread.get(key) || { count: 0, hasCritical: false };
      prevApp.count += 1;
      if (isCrit) prevApp.hasCritical = true;
      agendaAppUnread.set(key, prevApp);
    }
  }

  return {
    watchPropUnread,
    watchCheckUnread,
    agendaOccUnread,
    agendaAppUnread,
  };
}

// ---------------------------------------------------------------------------
// 5. Stream 4 Watchlist Correlation, Promotion & Sanitized Rendering (DF-6, UT-7)
// ---------------------------------------------------------------------------

/**
 * Match a Stream 4 permit/HAHC event or alert against active Watchlist properties by:
 *   1. Explicit `watchlist_id` / `watchPropertyId`
 *   2. Normalized 13-digit HCAD parcel ID overlap (`parseHcadAccountList`)
 *   3. Normalized `simplifySearchAddress` match across primary & secondary addresses
 */
export function findMatchingWatchPropertyForAlert(alertOrEvent = {}, watchedProperties = []) {
  if (!alertOrEvent || typeof alertOrEvent !== "object") return null;
  const activeProps = (Array.isArray(watchedProperties) ? watchedProperties : []).filter(
    (p) =>
      p &&
      p.trackerStatus !== "Archived (Tier 3)" &&
      !isRemovedWatchlistTombstone(p)
  );
  if (activeProps.length === 0) return null;

  const meta =
    alertOrEvent.permitMeta && typeof alertOrEvent.permitMeta === "object"
      ? alertOrEvent.permitMeta
      : alertOrEvent;

  const explicitId = String(meta.watchlist_id || alertOrEvent.watchPropertyId || "").trim();
  if (explicitId) {
    const byId = activeProps.find((p) => p.id === explicitId);
    if (byId) return byId;
  }

  const candidateHcadBlob = [
    meta.hcad_num || "",
    alertOrEvent.hcad || "",
    ...(Array.isArray(meta.related_hcads) ? meta.related_hcads : []),
  ]
    .filter(Boolean)
    .join(", ");
  const { hcadIds: alertHcads } = parseHcadAccountList(candidateHcadBlob);
  if (alertHcads.length > 0) {
    const alertHcadSet = new Set(alertHcads);
    const byHcad = activeProps.find((p) => {
      const { hcadIds } = parseHcadAccountList(p.hcadAccount || "");
      return hcadIds.some((id) => alertHcadSet.has(id));
    });
    if (byHcad) return byHcad;
  }

  const normAddr = simplifySearchAddress(meta.address || alertOrEvent.address || "").toUpperCase();
  if (normAddr) {
    const byAddr = activeProps.find((p) => {
      const candidates = [
        p.address || "",
        ...(Array.isArray(p.secondaryAddresses) ? p.secondaryAddresses : []),
        ...(Array.isArray(p.searchTerms?.altAddresses) ? p.searchTerms.altAddresses : []),
      ];
      return candidates.some((c) => simplifySearchAddress(c).toUpperCase() === normAddr);
    });
    if (byAddr) return byAddr;
  }

  return null;
}

/**
 * Construct a normalized Tier 1 Watchlist property dossier from a Stream 4 permit/asbestos alert
 * (`DF-6`), seeding combined heritage designations, `chk-14`/`chk-15` check stamps with `loggedAt`,
 * and harvested 8-digit COH `project_no` IDs in `ilmsProjectNo` and `searchTerms.projectNumbers`.
 */
export function buildWatchPropertyFromPermitAlert(
  alertOrEvent = {},
  {
    reviewer = "",
    nowIso = "",
    fallbackAddress = "",
    fallbackName = "",
    source = "",
    defaultTier = "1",
  } = {}
) {
  const effectiveNowIso = String(nowIso || "").trim() || new Date().toISOString();
  const todayLocal = toChicagoDateIso(effectiveNowIso);
  const meta =
    alertOrEvent?.permitMeta && typeof alertOrEvent.permitMeta === "object"
      ? alertOrEvent.permitMeta
      : alertOrEvent || {};
  const rec = meta.record && typeof meta.record === "object" ? meta.record : null;

  const cohList = Array.isArray(meta.coh_permits)
    ? meta.coh_permits.filter((x) => x && typeof x === "object")
    : rec && meta.alertKind !== "dshs"
    ? [rec]
    : [];
  const dshsList = Array.isArray(meta.dshs_notifications)
    ? meta.dshs_notifications.filter((x) => x && typeof x === "object")
    : rec && meta.alertKind === "dshs"
    ? [rec]
    : [];

  const hcadNorm = normalizeHcad13(meta.hcad_num || alertOrEvent?.hcad || "");
  const hasDigitsHcad = /^\d{13}$/.test(hcadNorm);
  const addr = sanitizeActivityText(
    meta.address || alertOrEvent?.address || fallbackAddress || hcadNorm
  );
  const title = sanitizeActivityText(
    meta.building_name || meta.title || alertOrEvent?.title || fallbackName || addr || hcadNorm
  );
  const propId = hasDigitsHcad
    ? `prop-watch-${hcadNorm}`
    : `prop-watch-${slugToken(addr || title)}`;

  const desig =
    [
      meta.landmark_name ? `City Landmark: ${sanitizeActivityText(meta.landmark_name)}` : "",
      meta.historic_district
        ? `Historic District: ${sanitizeActivityText(meta.historic_district)}`
        : "",
      sanitizeActivityText(meta.good_brick_summary || ""),
    ]
      .filter(Boolean)
      .join(" | ") || sanitizeActivityText(alertOrEvent?.designation || "");

  const harvestedProjects = [];
  for (const cp of cohList) {
    const pno = String(cp.project_no || "").trim();
    if (/^\d{8}$/.test(pno) && !harvestedProjects.includes(pno)) {
      harvestedProjects.push(pno);
    }
  }
  const allHcads = [
    ...(hasDigitsHcad ? [hcadNorm] : []),
    ...(Array.isArray(meta.related_hcads)
      ? meta.related_hcads.map((h) => normalizeHcad13(h)).filter((h) => /^\d{13}$/.test(h))
      : []),
  ];
  const uniqueHcads = Array.from(new Set(allHcads));

  const primaryCoh = cohList[0] || {};
  const primaryDshs = dshsList[0] || {};
  const entityCandidates = Array.from(
    new Set(
      [
        primaryCoh.buyer,
        primaryDshs.facility_owner,
        ...cohList.map((c) => c.buyer),
        ...dshsList.map((d) => d.facility_owner),
      ]
        .map((x) => sanitizeActivityText(x || ""))
        .filter(Boolean)
    )
  );
  const secondaryAddresses = Array.isArray(meta.secondary_addresses)
    ? meta.secondary_addresses.map((a) => sanitizeActivityText(a)).filter(Boolean)
    : [];
  const resolvedLat = meta.lat ?? alertOrEvent?.lat ?? null;
  const resolvedLng = meta.lng ?? meta.lon ?? alertOrEvent?.lng ?? null;
  const noteBody =
    alertOrEvent?.summary ||
    meta.preservation_note ||
    meta.headline ||
    `Promoted from Houston Building Atlas permit monitoring on ${todayLocal}.`;
  const noteText = alertOrEvent?.summary
    ? sanitizeActivityText(`Promoted from What's New & Daily Log alert on ${todayLocal}. ${noteBody}`)
    : sanitizeActivityText(noteBody);

  const rawProp = {
    id: propId,
    propertyName: title,
    address: addr,
    secondaryAddresses,
    hcadAccount: uniqueHcads.join(", "),
    buildingId: meta.building_id || alertOrEvent?.buildingId || "",
    yearBuilt: meta.year_built ?? alertOrEvent?.yearBuilt ?? "",
    yearRemodeled: meta.yr_remodel ?? "",
    architect: meta.architect || "",
    designation: desig,
    ownerOfRecord:
      meta.owner || primaryDshs.facility_owner || primaryCoh.owner_occupant || "",
    buyerRelatedEntities: primaryCoh.buyer || "",
    neighborhood: meta.neighborhood || "",
    tier: String(defaultTier || "1"),
    tierConfirmed: true,
    threatCategory: "Significant Building Threatened",
    trackerStatus: "Active / High Priority",
    reportedBy: reviewer,
    ilmsProjectNo: harvestedProjects[0] || "",
    searchTerms: {
      entities: entityCandidates,
      dbas: [],
      officers: [],
      legalKeywords: [],
      altAddresses: secondaryAddresses.slice(),
      projectNumbers: harvestedProjects,
    },
    searchTermsUpdatedAt:
      harvestedProjects.length > 0 || entityCandidates.length > 0 ? effectiveNowIso : "",
    keyDates: {
      monitoringStarted: todayLocal,
      asbestosAbatementWindow:
        primaryDshs.start_date && primaryDshs.end_date
          ? `${primaryDshs.start_date} to ${primaryDshs.end_date}`
          : "",
    },
    notes: noteText,
    lat: resolvedLat,
    lng: resolvedLng,
    coordSource: resolvedLat != null && resolvedLng != null ? "Atlas Parcel Centroid" : "",
    humanEdited: true,
    updatedAt: effectiveNowIso,
    updatedBy: reviewer,
  };
  if (source) rawProp.source = String(source);

  const baseProp = normalizeWatchProperty(rawProp);
  const eventLinks = sanitizeExternalLinksList(alertOrEvent?.links || []);

  if (cohList.length > 0) {
    const chk14 = findChecklistRow(baseProp.checklist, "chk-14");
    if (chk14) {
      chk14.status = "Flagged";
      chk14.humanStatusEdited = true;
      chk14.lastChecked = parseIsoDatePrefix(primaryCoh.date) || todayLocal;
      chk14.updatedAt = effectiveNowIso;
      chk14.statusChangedAt = effectiveNowIso;
      chk14.checks = [
        ...cohList.map((cp) => {
          const pno = sanitizeActivityText(cp.project_no || "N/A");
          const ptLbl = sanitizeActivityText(
            cp.permit_type_label || cp.permit_type || "City Permit"
          );
          const desc = sanitizeActivityText(
            cp.use_desc || cp.permit_desc || alertOrEvent?.summary || addr
          );
          const buyerSuffix = cp.buyer ? ` · Applicant: ${sanitizeActivityText(cp.buyer)}` : "";
          return {
            date: parseIsoDatePrefix(cp.date) || chk14.lastChecked,
            result: `${ptLbl} #${pno}: ${desc}${buyerSuffix}`,
            source: "manual",
            loggedAt: effectiveNowIso,
            links: eventLinks,
          };
        }),
        ...chk14.checks,
      ];
    }
  }

  if (dshsList.length > 0) {
    const chk15 = findChecklistRow(baseProp.checklist, "chk-15");
    if (chk15) {
      chk15.status = "Flagged";
      chk15.humanStatusEdited = true;
      chk15.lastChecked =
        parseIsoDatePrefix(primaryDshs.filing_date || primaryDshs.start_date) || todayLocal;
      chk15.updatedAt = effectiveNowIso;
      chk15.statusChangedAt = effectiveNowIso;
      chk15.checks = [
        ...dshsList.map((dn) => {
          const dLinks = sanitizeExternalLinksList([
            ...eventLinks,
            ...(dn.pdf_url ? [{ label: "DSHS Notification PDF", url: dn.pdf_url }] : []),
            ...(dn.print_action_url
              ? [{ label: "TX DSHS Record", url: dn.print_action_url }]
              : []),
          ]);
          const opSuffix = dn.abatement_contractor
            ? ` · Operator: ${sanitizeActivityText(dn.abatement_contractor)}`
            : "";
          return {
            date: parseIsoDatePrefix(dn.filing_date || dn.start_date) || chk15.lastChecked,
            result: `TX DSHS ${sanitizeActivityText(
              dn.work_type || "Asbestos/Demolition"
            )} #${sanitizeActivityText(dn.notification_number || "N/A")}: ${sanitizeActivityText(
              dn.work_description || alertOrEvent?.summary || addr
            )}${opSuffix}`,
            source: "manual",
            loggedAt: effectiveNowIso,
            links: dLinks,
          };
        }),
        ...chk15.checks,
      ];
      const dshsWithPrint = dshsList.find((dn) =>
        String(dn.print_action_url || "").includes("notificationPrintAction.do")
      );
      if (dshsWithPrint) {
        chk15.additionalResourceLabel = `DSHS #${sanitizeActivityText(
          dshsWithPrint.notification_number || "Record"
        )}`;
        chk15.additionalResourceUrl = String(dshsWithPrint.print_action_url);
      }
    }
  }

  return normalizeWatchProperty(baseProp);
}

export function buildEventAtlasMapHref(ev = {}) {
  if (!ev || typeof ev !== "object") return "";
  const kind = String(ev.kind || "");
  const stream = String(ev.stream || "");
  const isHahc = kind.startsWith("hahc_");
  const isPermitStream = stream === "permits";

  const normHcad = normalizeHcad13(ev.hcad || ev.permitMeta?.hcad_num || "");
  const validHcad = /^\d{13}$/.test(normHcad) ? normHcad : "";
  const buildingId = String(ev.buildingId || ev.permitMeta?.building_id || "").trim();
  const addr = sanitizeActivityText(ev.address || ev.permitMeta?.address || "");
  const rawLat = ev.lat ?? ev.permitMeta?.lat;
  const rawLng = ev.lng ?? ev.permitMeta?.lng;
  const latNum = Number(rawLat);
  const lngNum = Number(rawLng);
  const hasCoords =
    rawLat !== null &&
    rawLat !== undefined &&
    rawLat !== "" &&
    rawLng !== null &&
    rawLng !== undefined &&
    rawLng !== "" &&
    Number.isFinite(latNum) &&
    Number.isFinite(lngNum);

  if (!isPermitStream && !validHcad && !buildingId && !hasCoords) {
    return "";
  }

  const hashParams = new URLSearchParams();
  if (isHahc) {
    hashParams.set("layers", "permit_watch,hahc_coa,landmarks,historic_districts");
  } else if (isPermitStream) {
    hashParams.set("layers", "permit_watch,landmarks,historic_districts");
  } else {
    hashParams.set("layers", "permit_watch,plat_watch,landmarks,historic_districts");
  }

  if (validHcad) hashParams.set("hcad", validHcad);
  if (buildingId) hashParams.set("id", buildingId);
  if (addr) hashParams.set("q", addr);
  if (hasCoords) {
    hashParams.set("lat", latNum.toFixed(6));
    hashParams.set("lng", lngNum.toFixed(6));
    hashParams.set("z", "18");
  }
  return `index.html#${hashParams.toString()}`;
}

export function sanitizeActivityEventForRender(ev = {}) {
  const safeLinks = sanitizeExternalLinksList(ev?.links || []).map((lk) => ({
    label: sanitizeActivityText(lk.label),
    safeLabel: escapeHtmlText(sanitizeActivityText(lk.label)),
    url: lk.url,
    safeUrl: escapeHtmlText(lk.url),
  }));
  const atlasMapHref = buildEventAtlasMapHref(ev);
  return {
    id: String(ev?.id || ""),
    safeId: escapeHtmlText(ev?.id || ""),
    stream: String(ev?.stream || "watchlist"),
    streamLabel: ACTIVITY_STREAM_META[ev?.stream]?.shortLabel || "Activity",
    kind: String(ev?.kind || ""),
    severity: ev?.severity === "critical" || ev?.severity === "warning" ? ev.severity : "info",
    adverse: Boolean(ev?.adverse),
    localDate: parseIsoDatePrefix(ev?.localDate) || parseIsoDatePrefix(ev?.date),
    date: parseIsoDatePrefix(ev?.date) || parseIsoDatePrefix(ev?.localDate),
    safeTitle: escapeHtmlText(sanitizeActivityText(ev?.title || "")),
    safeSummary: escapeHtmlText(sanitizeActivityText(ev?.summary || "")),
    safeAddress: escapeHtmlText(sanitizeActivityText(ev?.address || "")),
    safeHcad: escapeHtmlText(sanitizeActivityText(ev?.hcad || "")),
    safeDesignation: escapeHtmlText(sanitizeActivityText(ev?.designation || "")),
    safeAppNo: escapeHtmlText(sanitizeActivityText(ev?.appNo || "")),
    safeCheckId: escapeHtmlText(String(ev?.checkId || "").toUpperCase()),
    yearBuilt: ev?.yearBuilt ?? null,
    atlasMapHref,
    safeAtlasMapHref: escapeHtmlText(atlasMapHref),
    safeLinks,
  };
}

export function formatActivityBriefingText({
  events = [],
  dailyRun = null,
  rangeLabel = "Since Last Visit",
  nowIso = "",
} = {}) {
  const effectiveNowIso = String(nowIso || "").trim() || new Date().toISOString();
  const todayLocal = toChicagoDateIso(effectiveNowIso);
  const lines = [
    `Preservation Houston - Plat & Property Monitor Briefing (${sanitizeActivityText(rangeLabel)})`,
    `Generated: ${todayLocal}`,
  ];

  if (dailyRun && typeof dailyRun === "object") {
    const okAg = Number(dailyRun.succeededAgencies ?? 8);
    const totAg = Number(dailyRun.totalAgencies ?? 8);
    const failed = Array.isArray(dailyRun.failedAgencies) ? dailyRun.failedAgencies : [];
    lines.push(
      failed.length === 0
        ? `Daily Sweep Health (${dailyRun.date || todayLocal}): ${okAg}/${totAg} agencies OK (${dailyRun.propertiesSwept ?? 0} watched properties swept)`
        : `Daily Sweep Health (${dailyRun.date || todayLocal}): DEGRADED - ${okAg}/${totAg} agencies OK (Failed: ${failed.join(", ")})`
    );
  }
  lines.push("");

  const sorted = sortEventsForBriefing(events);
  if (sorted.length === 0) {
    lines.push("No new findings or updates in this window.");
    return lines.join("\n").replace(/\u2014/g, " - ");
  }

  for (const ev of sorted) {
    const sevTag =
      ev.severity === "critical"
        ? "[CRITICAL]"
        : ev.severity === "warning"
        ? "[REVIEW]"
        : "[INFO]";
    const streamLbl = ACTIVITY_STREAM_META[ev.stream]?.shortLabel || ev.stream || "Update";
    lines.push(
      `- ${sevTag} [${ev.localDate || ev.date || todayLocal} | ${streamLbl}] ${sanitizeActivityText(ev.title)}`
    );
    if (ev.summary) {
      lines.push(`  ${sanitizeActivityText(ev.summary)}`);
    }
    const safeLinks = sanitizeExternalLinksList(ev.links || []);
    if (safeLinks.length > 0) {
      lines.push(`  Links: ${safeLinks.map((lk) => `${sanitizeActivityText(lk.label)} (${lk.url})`).join(" | ")}`);
    }
  }

  return lines.join("\n").replace(/\u2014/g, " - ");
}
