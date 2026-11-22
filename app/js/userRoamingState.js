/**
 * Per-User Cross-Device Roaming State & Conflict-Free Cloud Merge Engine.
 *
 * Keeps per-user reading progress (`caughtUpAt`, bidirectional `readLedger` read/unread
 * overrides), monitoring view preferences (`actStream`, `actGroupBy`, `lastMode`,
 * `cycleViewMode`, `reviewerName`), and Staff Workbench state (`auditDecisions`,
 * `inspectorWide`, `ecosystemTheme`) synchronized across work, home, and mobile browsers
 * via Preservation Houston's existing Google Workspace Apps Script endpoint, without
 * requiring an external server-side database.
 *
 * Pure ES module with zero top-level DOM/window globals so bare Node unit suites
 * (`tests/unit/run_all.mjs`) verify every merge, compaction, and network-fallback path.
 */

import { parseIsoEpochMs, sanitizeActivityText } from "./activityLogHelpers.js?v=20261113c";

export const ROAMING_SCHEMA_VERSION = 1;
export const ROAMING_STORAGE_PREFIX = "ph_atlas_roaming_user_v1:";
export const ROAMING_ANON_STORAGE_KEY = "ph_atlas_roaming_anon_v1";
export const ROAMING_DIRTY_PREFIX = "ph_atlas_roaming_dirty_v1:";
export const ROAMING_LAST_MIRROR_OWNER_KEY = "ph_atlas_roaming_mirror_owner_v1";
export const MAX_ROAMING_READ_LEDGER = 300;
export const MAX_ROAMING_AUDIT_DECISIONS = 300;
export const MAX_ROAMING_JSON_CHARS = 42000;

export const DEFAULT_SHEET_WEBHOOK_URL =
  "https://script.google.com/macros/s/AKfycbw1axAzEHZgcIKcdRUAjltDIsq505PJRalUMOfmJJa07qR-RCdsWBAwPv1gjN3sJlEbtg/exec";
export const DEFAULT_SHEET_PUBLISHED_CSV_URL =
  "https://docs.google.com/spreadsheets/d/e/2PACX-1vS6oiB-T1JH-oy8coeDD1Gqey_l9rP7Its3NWe7KerNQyBF_Mqrx1NHPLWV8yKTtgS0CTXoJySi3jcO/pub?gid=1876369840&single=true&output=csv";

const ALLOWED_PREF_KEYS = new Set([
  "actStream",
  "actGroupBy",
  "lastMode",
  "cycleViewMode",
  "reviewerName",
  "inspectorWide",
  "ecosystemTheme",
  "actRange",
]);

export function normalizeStaffEmail(rawEmail) {
  const s = String(rawEmail ?? "")
    .trim()
    .toLowerCase();
  if (!s || !s.includes("@") || s.length > 180) return "";
  return s;
}

export function getRoamingStorageKey(rawEmail) {
  const email = normalizeStaffEmail(rawEmail);
  return email ? `${ROAMING_STORAGE_PREFIX}${email}` : ROAMING_ANON_STORAGE_KEY;
}

export function getRoamingDirtyKey(rawEmail) {
  const email = normalizeStaffEmail(rawEmail);
  return email ? `${ROAMING_DIRTY_PREFIX}${email}` : "";
}

export function createEmptyRoamingState() {
  return {
    schemaVersion: ROAMING_SCHEMA_VERSION,
    updatedAt: "",
    caughtUpAt: "",
    caughtUpUpdatedAt: 0,
    readLedger: {},
    prefs: {},
    auditDecisions: {},
  };
}

function toSafeEpochMs(val, fallback = 0) {
  if (typeof val === "number" && Number.isFinite(val) && val > 0) {
    return Math.trunc(val);
  }
  const parsed = parseIsoEpochMs(val);
  return parsed !== null && parsed > 0 ? Math.trunc(parsed) : fallback;
}

function sanitizePlainValue(val) {
  if (typeof val === "string") {
    return sanitizeActivityText(val).slice(0, 500);
  }
  if (typeof val === "boolean" || (typeof val === "number" && Number.isFinite(val))) {
    return val;
  }
  return "";
}

function readFlagSortRank(r) {
  if (r === 0) return 0;
  if (r === 1) return 1;
  return 2;
}

function pruneReadLedgerEntries(rawLedger, _caughtUpAt = "", maxEntries = MAX_ROAMING_READ_LEDGER) {
  if (!rawLedger || typeof rawLedger !== "object") return {};
  const entries = [];
  for (const [rawId, rawEntry] of Object.entries(rawLedger)) {
    const id = sanitizeActivityText(rawId).slice(0, 180);
    if (!id || !rawEntry || typeof rawEntry !== "object") continue;
    const r = rawEntry.r === -1 ? -1 : rawEntry.r === 0 || rawEntry.read === false ? 0 : 1;
    const t = toSafeEpochMs(rawEntry.t ?? rawEntry.at, 1);
    entries.push([id, { r, t }]);
  }

  // Sort monotonically with LWW join order (`t` descending first, then `r` tie-breaker rank,
  // then code-point `id`) so top-K truncation is strictly associative and newer un-pins (`r === 1`)
  // are never evicted behind older `Keep Unread` (`r === 0`) pins.
  entries.sort((a, b) => {
    if (b[1].t !== a[1].t) return b[1].t - a[1].t;
    const ra = readFlagSortRank(a[1].r);
    const rb = readFlagSortRank(b[1].r);
    if (ra !== rb) return ra - rb;
    return a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0;
  });

  const out = {};
  const cap = Math.max(1, Number(maxEntries) || MAX_ROAMING_READ_LEDGER);
  for (const [id, entry] of entries.slice(0, cap)) {
    out[id] = entry;
  }
  return out;
}

function pruneAuditDecisions(rawDecisions, maxEntries = MAX_ROAMING_AUDIT_DECISIONS) {
  if (!rawDecisions || typeof rawDecisions !== "object") return {};
  const entries = [];
  for (const [rawKey, rawVal] of Object.entries(rawDecisions)) {
    const key = sanitizeActivityText(rawKey).slice(0, 120);
    if (!key || !rawVal || typeof rawVal !== "object") continue;
    const status = sanitizeActivityText(rawVal.status || "").slice(0, 40);
    if (!status) continue;
    const at = toSafeEpochMs(rawVal.at ?? rawVal.updatedAt ?? rawVal.updated_at ?? rawVal.decidedAt, 1);
    const clean = {
      status,
      at,
      updated_at: new Date(at).toISOString(),
    };
    if (rawVal.hcad_num) clean.hcad_num = sanitizeActivityText(rawVal.hcad_num).slice(0, 32);
    if (rawVal.category) clean.category = sanitizeActivityText(rawVal.category).slice(0, 64);
    if (rawVal.suggestedYear !== undefined && rawVal.suggestedYear !== null && rawVal.suggestedYear !== "") {
      const yr = Number(rawVal.suggestedYear);
      if (Number.isFinite(yr)) clean.suggestedYear = Math.trunc(yr);
    }
    if (rawVal.note) clean.note = sanitizeActivityText(rawVal.note).slice(0, 200);
    entries.push([key, clean]);
  }
  entries.sort((a, b) => b[1].at - a[1].at || (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
  const out = {};
  const cap = Math.max(1, Number(maxEntries) || MAX_ROAMING_AUDIT_DECISIONS);
  for (const [k, v] of entries.slice(0, cap)) {
    out[k] = v;
  }
  return out;
}

export function normalizeRoamingState(raw, { maxReadEntries = MAX_ROAMING_READ_LEDGER, maxAuditEntries = MAX_ROAMING_AUDIT_DECISIONS } = {}) {
  if (!raw || typeof raw !== "object") {
    return createEmptyRoamingState();
  }
  const caughtUpRaw = sanitizeActivityText(raw.caughtUpAt || "");
  const caughtUpMs = parseIsoEpochMs(caughtUpRaw);
  const caughtUpAt = caughtUpMs !== null ? new Date(caughtUpMs).toISOString() : "";
  const caughtUpUpdatedAt = toSafeEpochMs(
    raw.caughtUpUpdatedAt,
    caughtUpMs !== null ? caughtUpMs : 0
  );

  const mergedRawLedger = { ...(raw.readLedger && typeof raw.readLedger === "object" ? raw.readLedger : {}) };
  if (Array.isArray(raw.readEventIds)) {
    const legacyTs = caughtUpMs !== null ? caughtUpMs : 1;
    for (const id of raw.readEventIds) {
      const cleanId = sanitizeActivityText(id).slice(0, 180);
      if (cleanId && !mergedRawLedger[cleanId]) {
        mergedRawLedger[cleanId] = { r: 1, t: legacyTs };
      }
    }
  }
  if (Array.isArray(raw.unreadEventIds)) {
    const legacyUnreadTs = (caughtUpMs !== null ? caughtUpMs : 1) + 1;
    for (const id of raw.unreadEventIds) {
      const cleanId = sanitizeActivityText(id).slice(0, 180);
      if (cleanId && !mergedRawLedger[cleanId]) {
        mergedRawLedger[cleanId] = { r: 0, t: legacyUnreadTs };
      }
    }
  }

  const readLedger = pruneReadLedgerEntries(mergedRawLedger, caughtUpAt, maxReadEntries);

  const prefs = {};
  if (raw.prefs && typeof raw.prefs === "object") {
    for (const [k, v] of Object.entries(raw.prefs)) {
      if (!ALLOWED_PREF_KEYS.has(k)) continue;
      if (v && typeof v === "object" && "v" in v) {
        prefs[k] = { v: sanitizePlainValue(v.v), t: toSafeEpochMs(v.t, 1) };
      } else if (v !== undefined && v !== null) {
        prefs[k] = { v: sanitizePlainValue(v), t: 1 };
      }
    }
  }

  const auditDecisions = pruneAuditDecisions(raw.auditDecisions, maxAuditEntries);

  let maxMutationMs = caughtUpUpdatedAt;
  for (const e of Object.values(readLedger)) {
    if (e.t > maxMutationMs) maxMutationMs = e.t;
  }
  for (const p of Object.values(prefs)) {
    if (p.t > maxMutationMs) maxMutationMs = p.t;
  }
  for (const d of Object.values(auditDecisions)) {
    if (d.at > maxMutationMs) maxMutationMs = d.at;
  }
  const explicitUpdatedMs = parseIsoEpochMs(raw.updatedAt);
  if (explicitUpdatedMs !== null && explicitUpdatedMs > maxMutationMs) {
    maxMutationMs = explicitUpdatedMs;
  }

  return {
    schemaVersion: ROAMING_SCHEMA_VERSION,
    updatedAt: maxMutationMs > 0 ? new Date(maxMutationMs).toISOString() : "",
    caughtUpAt,
    caughtUpUpdatedAt,
    readLedger,
    prefs,
    auditDecisions,
  };
}

/**
 * Deterministic, commutative, associative, idempotent merge of two roaming states.
 * Safe across simultaneous open browser tabs on work and home computers.
 */
export function mergeUserRoamingStates(stateA, stateB) {
  const a = normalizeRoamingState(stateA);
  const b = normalizeRoamingState(stateB);

  let caughtUpAt = a.caughtUpAt;
  let caughtUpUpdatedAt = a.caughtUpUpdatedAt;
  if (b.caughtUpUpdatedAt > a.caughtUpUpdatedAt) {
    caughtUpAt = b.caughtUpAt;
    caughtUpUpdatedAt = b.caughtUpUpdatedAt;
  } else if (b.caughtUpUpdatedAt === a.caughtUpUpdatedAt) {
    const aMs = parseIsoEpochMs(a.caughtUpAt) ?? -1;
    const bMs = parseIsoEpochMs(b.caughtUpAt) ?? -1;
    if (bMs > aMs) {
      caughtUpAt = b.caughtUpAt;
    }
  }

  const combinedLedger = {};
  const allEventIds = new Set([
    ...Object.keys(a.readLedger),
    ...Object.keys(b.readLedger),
  ]);
  for (const id of allEventIds) {
    const ea = a.readLedger[id];
    const eb = b.readLedger[id];
    if (ea && !eb) {
      combinedLedger[id] = { ...ea };
    } else if (eb && !ea) {
      combinedLedger[id] = { ...eb };
    } else if (eb.t > ea.t) {
      combinedLedger[id] = { ...eb };
    } else if (ea.t > eb.t) {
      combinedLedger[id] = { ...ea };
    } else {
      // Exact millisecond tie: preserve explicit unread (`r: 0`) first, then `r: 1`, then `r: -1`
      const winR = readFlagSortRank(eb.r) < readFlagSortRank(ea.r) ? eb.r : ea.r;
      combinedLedger[id] = { r: winR, t: ea.t };
    }
  }

  const prefs = {};
  const allPrefKeys = new Set([...Object.keys(a.prefs), ...Object.keys(b.prefs)]);
  for (const k of allPrefKeys) {
    const pa = a.prefs[k];
    const pb = b.prefs[k];
    if (pa && !pb) prefs[k] = { ...pa };
    else if (pb && !pa) prefs[k] = { ...pb };
    else if (pb.t > pa.t) prefs[k] = { ...pb };
    else if (pa.t > pb.t) prefs[k] = { ...pa };
    else {
      const sigA = `${typeof pa.v}:${String(pa.v)}`;
      const sigB = `${typeof pb.v}:${String(pb.v)}`;
      prefs[k] = sigB > sigA ? { ...pb } : { ...pa };
    }
  }

  const auditDecisions = {};
  const allAuditKeys = new Set([
    ...Object.keys(a.auditDecisions),
    ...Object.keys(b.auditDecisions),
  ]);
  for (const k of allAuditKeys) {
    const da = a.auditDecisions[k];
    const db = b.auditDecisions[k];
    if (da && !db) auditDecisions[k] = { ...da };
    else if (db && !da) auditDecisions[k] = { ...db };
    else if (db.at > da.at) auditDecisions[k] = { ...db };
    else if (da.at > db.at) auditDecisions[k] = { ...da };
    else auditDecisions[k] = JSON.stringify(db) > JSON.stringify(da) ? { ...db } : { ...da };
  }

  const mergedUpdatedMs = Math.max(
    parseIsoEpochMs(a.updatedAt) ?? 0,
    parseIsoEpochMs(b.updatedAt) ?? 0,
    caughtUpUpdatedAt
  );

  return normalizeRoamingState({
    schemaVersion: ROAMING_SCHEMA_VERSION,
    updatedAt: mergedUpdatedMs > 0 ? new Date(mergedUpdatedMs).toISOString() : "",
    caughtUpAt,
    caughtUpUpdatedAt,
    readLedger: combinedLedger,
    prefs,
    auditDecisions,
  });
}

export function areRoamingStatesEquivalent(stateA, stateB) {
  const a = normalizeRoamingState(stateA);
  const b = normalizeRoamingState(stateB);
  return JSON.stringify(a) === JSON.stringify(b);
}

/**
 * Derive `{ caughtUpAt, readEventIds, unreadEventIds }` for `computeWatermarkState`.
 */
export function deriveWatermarkInputsFromRoamingState(state, legacyReadIds = []) {
  const norm = normalizeRoamingState(state);
  const readSet = new Set();
  const unreadSet = new Set();

  for (const [id, entry] of Object.entries(norm.readLedger)) {
    if (entry.r === 0) {
      unreadSet.add(id);
    } else if (entry.r === 1) {
      readSet.add(id);
    }
  }

  if (Array.isArray(legacyReadIds)) {
    for (const rawId of legacyReadIds) {
      const id = sanitizeActivityText(rawId);
      if (id && !unreadSet.has(id) && norm.readLedger[id]?.r !== -1) {
        readSet.add(id);
      }
    }
  }

  return {
    caughtUpAt: norm.caughtUpAt,
    readEventIds: Array.from(readSet),
    unreadEventIds: Array.from(unreadSet),
  };
}

export function applyMarkCaughtUpToRoamingState(
  state,
  { caughtUpAt = "", readEventIds = [], nowMs = Date.now() } = {}
) {
  const base = normalizeRoamingState(state);
  const bulkTs = toSafeEpochMs(nowMs, Date.now());
  const explicitTs = bulkTs + 1;
  const nextLedger = {};
  for (const [id, entry] of Object.entries(base.readLedger)) {
    if (entry.r === 0 || entry.r === 1) {
      // Stamp explicit Keep Unread pins (`r === 0`) and explicit un-pins (`r === 1`) at `explicitTs`
      // so bulk `readEventIds` at `bulkTs` can never evict them under top-300 `(t, rank, id)` pruning.
      nextLedger[id] = { r: entry.r, t: Math.max(entry.t, explicitTs) };
    } else {
      nextLedger[id] = { ...entry };
    }
  }
  const cappedBulk = Array.isArray(readEventIds) ? readEventIds.slice(0, 240) : [];
  for (const rawId of cappedBulk) {
    const id = sanitizeActivityText(rawId).slice(0, 180);
    if (id && (!nextLedger[id] || nextLedger[id].r === -1)) {
      nextLedger[id] = { r: 1, t: bulkTs };
    }
  }
  return normalizeRoamingState({
    ...base,
    updatedAt: new Date(explicitTs).toISOString(),
    caughtUpAt: caughtUpAt || new Date(bulkTs).toISOString(),
    caughtUpUpdatedAt: explicitTs,
    readLedger: nextLedger,
  });
}

export function applyRestoreRoamingSnapshot(previousState, nowMs = Date.now(), currentState = null) {
  const prev = normalizeRoamingState(previousState);
  const curr = currentState ? normalizeRoamingState(currentState) : null;
  const ts = toSafeEpochMs(nowMs, Date.now());
  const bumpedLedger = {};
  if (curr) {
    for (const id of Object.keys(curr.readLedger)) {
      if (!prev.readLedger[id]) {
        bumpedLedger[id] = { r: -1, t: ts };
      }
    }
  }
  for (const [id, entry] of Object.entries(prev.readLedger)) {
    bumpedLedger[id] = { r: entry.r, t: ts };
  }
  return normalizeRoamingState({
    ...prev,
    updatedAt: new Date(ts).toISOString(),
    caughtUpAt: prev.caughtUpAt,
    caughtUpUpdatedAt: ts,
    readLedger: bumpedLedger,
  });
}

export function applyToggleEventReadState(state, eventId, isRead, nowMs = Date.now()) {
  const base = normalizeRoamingState(state);
  const id = sanitizeActivityText(eventId).slice(0, 180);
  if (!id) return base;
  const ts = toSafeEpochMs(nowMs, Date.now());
  return normalizeRoamingState({
    ...base,
    updatedAt: new Date(ts).toISOString(),
    readLedger: {
      ...base.readLedger,
      [id]: { r: isRead ? 1 : 0, t: ts },
    },
  });
}

export function applyBatchEventsReadState(state, eventIds = [], isRead = true, nowMs = Date.now()) {
  const base = normalizeRoamingState(state);
  const ids = (Array.isArray(eventIds) ? eventIds : [])
    .map((x) => sanitizeActivityText(x).slice(0, 180))
    .filter(Boolean);
  if (!ids.length) return base;
  const ts = toSafeEpochMs(nowMs, Date.now());
  const nextLedger = { ...base.readLedger };
  const flag = isRead ? 1 : 0;
  for (const id of ids) {
    nextLedger[id] = { r: flag, t: ts };
  }
  return normalizeRoamingState({
    ...base,
    updatedAt: new Date(ts).toISOString(),
    readLedger: nextLedger,
  });
}

export function applyRoamingPrefUpdate(state, key, value, nowMs = Date.now()) {
  const base = normalizeRoamingState(state);
  if (!ALLOWED_PREF_KEYS.has(key)) return base;
  const ts = toSafeEpochMs(nowMs, Date.now());
  return normalizeRoamingState({
    ...base,
    updatedAt: new Date(ts).toISOString(),
    prefs: {
      ...base.prefs,
      [key]: { v: sanitizePlainValue(value), t: ts },
    },
  });
}

export function getRoamingPrefValue(state, key, fallbackValue = undefined) {
  const norm = normalizeRoamingState(state);
  if (norm.prefs[key] && norm.prefs[key].v !== undefined && norm.prefs[key].v !== "") {
    return norm.prefs[key].v;
  }
  if (norm.prefs[key] && typeof norm.prefs[key].v === "boolean") {
    return norm.prefs[key].v;
  }
  return fallbackValue;
}

export function applyRoamingAuditDecision(state, itemKey, decisionObj, nowMs = Date.now()) {
  const base = normalizeRoamingState(state);
  const k = sanitizeActivityText(itemKey).slice(0, 120);
  if (!k || !decisionObj || typeof decisionObj !== "object") return base;
  const ts = toSafeEpochMs(decisionObj.at ?? nowMs, Date.now());
  return normalizeRoamingState({
    ...base,
    updatedAt: new Date(ts).toISOString(),
    auditDecisions: {
      ...base.auditDecisions,
      [k]: { ...decisionObj, at: ts },
    },
  });
}

export function compactRoamingStateForTransport(state, maxChars = MAX_ROAMING_JSON_CHARS) {
  let norm = normalizeRoamingState(state);
  let serialized = JSON.stringify(norm);
  if (serialized.length <= maxChars) return norm;

  const steps = [
    [180, 180],
    [100, 100],
    [60, 50],
    [25, 20],
  ];
  for (const [rCap, aCap] of steps) {
    norm = normalizeRoamingState(norm, { maxReadEntries: rCap, maxAuditEntries: aCap });
    serialized = JSON.stringify(norm);
    if (serialized.length <= maxChars) return norm;
  }
  return norm;
}

// ---------------------------------------------------------------------------
// Browser Storage Hydration, Legacy Key Mirroring & Cloud Webhook Transport
// ---------------------------------------------------------------------------

function resolveStorage(explicitStorage = null) {
  if (explicitStorage) return explicitStorage;
  try {
    if (typeof globalThis !== "undefined" && globalThis.localStorage) {
      return globalThis.localStorage;
    }
  } catch (_e) {}
  return null;
}

function safeParseJson(raw, fallback = null) {
  if (!raw || typeof raw !== "string") return fallback;
  try {
    return JSON.parse(raw);
  } catch (_e) {
    return fallback;
  }
}

export function extractLegacyLocalStateSnapshot(storage = null) {
  const st = resolveStorage(storage);
  if (!st) return createEmptyRoamingState();

  const rawCaughtUp = safeParseJson(st.getItem("phpam:caughtUpAt"), null);
  const caughtUpAt = typeof rawCaughtUp === "string" ? rawCaughtUp : "";
  const rawReadIds = safeParseJson(st.getItem("phpam:readEventIds"), []);
  const readEventIds = Array.isArray(rawReadIds) ? rawReadIds : [];

  const prefs = {};
  const actStream = safeParseJson(st.getItem("phpam:actStream"), null);
  if (typeof actStream === "string" && actStream) prefs.actStream = { v: actStream, t: 1 };
  const actGroupBy = safeParseJson(st.getItem("phpam:actGroupBy"), null);
  if (typeof actGroupBy === "string" && actGroupBy) prefs.actGroupBy = { v: actGroupBy, t: 1 };
  const lastMode = safeParseJson(st.getItem("phpam:lastMode"), null);
  if (typeof lastMode === "string" && lastMode) prefs.lastMode = { v: lastMode, t: 1 };
  const reviewer = safeParseJson(st.getItem("phpam:reviewer"), null);
  if (typeof reviewer === "string" && reviewer && reviewer !== "Preservation Houston") {
    prefs.reviewerName = { v: reviewer, t: 1 };
  }
  const wideRaw = st.getItem("atlas_inspector_wide");
  if (wideRaw === "1" || wideRaw === "0") {
    prefs.inspectorWide = { v: wideRaw === "1", t: 1 };
  }
  const themeRaw = st.getItem("hba:ecosystem:theme");
  if (themeRaw === "light" || themeRaw === "dark") {
    prefs.ecosystemTheme = { v: themeRaw, t: 1 };
  }

  const rawAudit = safeParseJson(st.getItem("ph_atlas_audit_decisions_v1"), {});
  const auditDecisions = {};
  if (rawAudit && typeof rawAudit === "object" && !Array.isArray(rawAudit)) {
    for (const [k, v] of Object.entries(rawAudit)) {
      if (v && typeof v === "object" && v.status) {
        auditDecisions[k] = {
          ...v,
          at: toSafeEpochMs(v.at ?? v.decidedAt ?? v.updatedAt ?? v.updated_at, 1),
        };
      }
    }
  }

  return normalizeRoamingState({
    caughtUpAt,
    readEventIds,
    prefs,
    auditDecisions,
  });
}

export function mirrorRoamingStateToLegacyStorage(
  state,
  storage = null,
  ownerEmail = null,
  { forceResetMissing = false } = {}
) {
  const st = resolveStorage(storage);
  if (!st) return;
  const norm = normalizeRoamingState(state);
  const wmInputs = deriveWatermarkInputsFromRoamingState(norm);
  try {
    const prevOwner = st.getItem(ROAMING_LAST_MIRROR_OWNER_KEY);
    const nextOwner = ownerEmail !== null ? normalizeStaffEmail(ownerEmail) : null;
    const ownerSwitched =
      forceResetMissing || (nextOwner !== null && prevOwner !== null && prevOwner !== nextOwner);

    st.setItem("phpam:caughtUpAt", JSON.stringify(wmInputs.caughtUpAt));
    st.setItem("phpam:readEventIds", JSON.stringify(wmInputs.readEventIds));

    const actStream = getRoamingPrefValue(norm, "actStream");
    if (typeof actStream === "string" && actStream) {
      st.setItem("phpam:actStream", JSON.stringify(actStream));
    } else if (ownerSwitched) {
      st.removeItem("phpam:actStream");
    }

    const actGroupBy = getRoamingPrefValue(norm, "actGroupBy");
    if (typeof actGroupBy === "string" && actGroupBy) {
      st.setItem("phpam:actGroupBy", JSON.stringify(actGroupBy));
    } else if (ownerSwitched) {
      st.removeItem("phpam:actGroupBy");
    }

    const lastMode = getRoamingPrefValue(norm, "lastMode");
    if (typeof lastMode === "string" && lastMode) {
      st.setItem("phpam:lastMode", JSON.stringify(lastMode));
    } else if (ownerSwitched) {
      st.removeItem("phpam:lastMode");
    }

    const reviewerName = getRoamingPrefValue(norm, "reviewerName");
    if (typeof reviewerName === "string" && reviewerName) {
      st.setItem("phpam:reviewer", JSON.stringify(reviewerName));
    } else if (ownerSwitched) {
      st.removeItem("phpam:reviewer");
    }

    const wide = getRoamingPrefValue(norm, "inspectorWide");
    if (typeof wide === "boolean") {
      st.setItem("atlas_inspector_wide", wide ? "1" : "0");
    } else if (ownerSwitched) {
      st.removeItem("atlas_inspector_wide");
    }

    const theme = getRoamingPrefValue(norm, "ecosystemTheme");
    if (theme === "light" || theme === "dark") {
      st.setItem("hba:ecosystem:theme", theme);
    } else if (ownerSwitched) {
      st.removeItem("hba:ecosystem:theme");
    }

    if (ownerSwitched) {
      if (Object.keys(norm.auditDecisions).length > 0) {
        st.setItem("ph_atlas_audit_decisions_v1", JSON.stringify(norm.auditDecisions));
      } else {
        st.removeItem("ph_atlas_audit_decisions_v1");
      }
    } else if (Object.keys(norm.auditDecisions).length > 0) {
      const existingAudit = safeParseJson(st.getItem("ph_atlas_audit_decisions_v1"), {}) || {};
      const mergedAudit = { ...existingAudit };
      for (const [k, d] of Object.entries(norm.auditDecisions)) {
        const prevAt = toSafeEpochMs(existingAudit[k]?.at ?? existingAudit[k]?.decidedAt, 0);
        if (!existingAudit[k] || d.at >= prevAt) {
          mergedAudit[k] = { ...existingAudit[k], ...d };
        }
      }
      st.setItem("ph_atlas_audit_decisions_v1", JSON.stringify(mergedAudit));
    }

    if (nextOwner !== null) {
      st.setItem(ROAMING_LAST_MIRROR_OWNER_KEY, nextOwner);
    }
  } catch (_e) {}
}

export function loadLocalUserRoamingState({ email = "", storage = null } = {}) {
  const st = resolveStorage(storage);
  if (!st) return createEmptyRoamingState();
  const normEmail = normalizeStaffEmail(email);
  const key = getRoamingStorageKey(normEmail);
  const rawStored = safeParseJson(st.getItem(key), null);

  let mirrorOwner = null;
  try {
    mirrorOwner = st.getItem(ROAMING_LAST_MIRROR_OWNER_KEY);
  } catch (_e) {}

  // Only fold shared legacy keys when migrating a pre-roaming browser (`mirrorOwner === null`)
  // or when the legacy mirror already belongs to this same user (`mirrorOwner === normEmail`).
  const canReadLegacyMirror = mirrorOwner === null || mirrorOwner === normEmail;
  if (!canReadLegacyMirror) {
    return rawStored ? normalizeRoamingState(rawStored) : createEmptyRoamingState();
  }

  const legacy = extractLegacyLocalStateSnapshot(st);
  if (!rawStored) {
    return legacy;
  }
  return mergeUserRoamingStates(rawStored, legacy);
}

export function saveLocalUserRoamingState(
  state,
  { email = "", storage = null, markDirty = false, mirrorLegacy = true } = {}
) {
  const st = resolveStorage(storage);
  const compacted = compactRoamingStateForTransport(state);
  if (!st) return compacted;

  const normEmail = normalizeStaffEmail(email);
  const key = getRoamingStorageKey(normEmail);
  try {
    st.setItem(key, JSON.stringify(compacted));
    const dirtyKey = getRoamingDirtyKey(normEmail);
    if (dirtyKey) {
      if (markDirty) st.setItem(dirtyKey, "1");
      else st.removeItem(dirtyKey);
    }
    if (mirrorLegacy) {
      mirrorRoamingStateToLegacyStorage(compacted, st, normEmail);
    }
  } catch (_e) {}
  return compacted;
}

export function isLocalUserRoamingStateDirty({ email = "", storage = null } = {}) {
  const st = resolveStorage(storage);
  const dirtyKey = getRoamingDirtyKey(email);
  if (!st || !dirtyKey) return false;
  try {
    return st.getItem(dirtyKey) === "1";
  } catch (_e) {
    return false;
  }
}

export function clearLegacyMirrorOnSignOut(storage = null) {
  const st = resolveStorage(storage);
  if (!st) return;
  try {
    const anonRaw = safeParseJson(st.getItem(ROAMING_ANON_STORAGE_KEY), null);
    const anon = normalizeRoamingState(anonRaw);
    mirrorRoamingStateToLegacyStorage(anon, st, "", { forceResetMissing: true });
    st.setItem(ROAMING_LAST_MIRROR_OWNER_KEY, "");
  } catch (_e) {}
}

/**
 * Shared 1-round-trip `verify_admin` hydration helper used by both `curatedEdits.js`
 * (`app/index.html`) and `app/plat-monitor.html` so anonymous drafts, first-time cloud
 * seeding, and dirty-state tracking never drift across apps.
 */
export function hydrateRoamingStateFromVerifyResponse({
  verifyData = {},
  email = "",
  storage = null,
} = {}) {
  const st = resolveStorage(storage);
  const resolvedEmail =
    normalizeStaffEmail(verifyData?.email || email) || String(email || "").trim();
  if (!verifyData || verifyData.supportsRoamingSync !== true || !st) {
    return {
      supportsRoamingSync: false,
      resolvedEmail,
      state: null,
      needsPush: false,
    };
  }

  let localForUser = loadLocalUserRoamingState({ email: resolvedEmail, storage: st });
  try {
    const anonDraft = safeParseJson(st.getItem(ROAMING_ANON_STORAGE_KEY), null);
    if (anonDraft && typeof anonDraft === "object") {
      localForUser = mergeUserRoamingStates(localForUser, anonDraft);
      st.removeItem(ROAMING_ANON_STORAGE_KEY);
    }
  } catch (_e) {}

  if (verifyData.userState && typeof verifyData.userState === "object") {
    const mergedCandidate = mergeUserRoamingStates(localForUser, verifyData.userState);
    const needsPush = !areRoamingStatesEquivalent(verifyData.userState, mergedCandidate);
    const saved = saveLocalUserRoamingState(mergedCandidate, {
      email: resolvedEmail,
      storage: st,
      markDirty: needsPush,
      mirrorLegacy: true,
    });
    return {
      supportsRoamingSync: true,
      resolvedEmail,
      state: saved,
      needsPush,
    };
  }

  const needsSeedPush = Boolean(localForUser.updatedAt);
  const seeded = saveLocalUserRoamingState(localForUser, {
    email: resolvedEmail,
    storage: st,
    markDirty: needsSeedPush,
    mirrorLegacy: true,
  });
  return {
    supportsRoamingSync: true,
    resolvedEmail,
    state: seeded,
    needsPush: needsSeedPush,
  };
}

export async function fetchCloudUserRoamingState({
  webhookUrl = "",
  session = null,
  fetchImpl = null,
} = {}) {
  const url = String(webhookUrl || "").trim();
  const email = normalizeStaffEmail(session?.email);
  const fn = fetchImpl || (typeof fetch === "function" ? fetch : null);
  if (!url || !email || !session?.authorized || !fn) {
    return { ok: false, skipped: true };
  }
  try {
    const res = await fn(url, {
      method: "POST",
      headers: { "Content-Type": "text/plain;charset=utf-8" },
      body: JSON.stringify({
        action: "get_user_state",
        admin_email: email,
        admin_passkey: session.adminPasskey || "",
        google_id_token: session.googleIdToken || "",
      }),
    });
    if (!res || !res.ok) {
      return { ok: false, offlineOrStub: true, status: res?.status || 0 };
    }
    const text = await res.text();
    const data = safeParseJson(text, null);
    if (!data || typeof data !== "object" || data.ok !== true) {
      return { ok: false, offlineOrStub: true };
    }
    return {
      ok: true,
      email: normalizeStaffEmail(data.email || email),
      userState: data.userState ? normalizeRoamingState(data.userState) : null,
      hasRemoteRecord: Boolean(data.userState),
    };
  } catch (_e) {
    return { ok: false, offlineOrStub: true };
  }
}

export async function pushCloudUserRoamingState({
  webhookUrl = "",
  session = null,
  state = null,
  fetchImpl = null,
} = {}) {
  const url = String(webhookUrl || "").trim();
  const email = normalizeStaffEmail(session?.email);
  const fn = fetchImpl || (typeof fetch === "function" ? fetch : null);
  if (!url || !email || !session?.authorized || !fn) {
    return { ok: false, skipped: true };
  }
  const compacted = compactRoamingStateForTransport(state);
  try {
    const res = await fn(url, {
      method: "POST",
      headers: { "Content-Type": "text/plain;charset=utf-8" },
      body: JSON.stringify({
        action: "put_user_state",
        admin_email: email,
        admin_passkey: session.adminPasskey || "",
        google_id_token: session.googleIdToken || "",
        user_state: compacted,
      }),
    });
    if (!res || !res.ok) {
      return { ok: false, offlineOrStub: true, status: res?.status || 0 };
    }
    const text = await res.text();
    const data = safeParseJson(text, null);
    if (!data || typeof data !== "object" || data.ok !== true) {
      return { ok: false, offlineOrStub: true };
    }
    return {
      ok: true,
      email: normalizeStaffEmail(data.email || email),
      userState: data.userState ? normalizeRoamingState(data.userState) : compacted,
    };
  } catch (_e) {
    return { ok: false, offlineOrStub: true };
  }
}

/**
 * Full two-way pull-merge-push cycle for an authenticated staff session.
 * - Merges any pre-sign-in anonymous progress (`includeAnonState: true` on sign-in)
 * - Pulls remote cloud state (`get_user_state`)
 * - Computes conflict-free merged state
 * - Pushes back (`put_user_state`) only if local had unpushed mutations or `localWasDirty`
 */
export async function syncUserRoamingStateWithCloud({
  webhookUrl = "",
  session = null,
  storage = null,
  fetchImpl = null,
  includeAnonState = false,
} = {}) {
  const st = resolveStorage(storage);
  const email = normalizeStaffEmail(session?.email);
  if (!email || !session?.authorized) {
    const localOnly = loadLocalUserRoamingState({ email: "", storage: st });
    return { ok: false, skipped: true, state: localOnly, changedFromLocal: false };
  }

  let localState = loadLocalUserRoamingState({ email, storage: st });
  if (includeAnonState && st) {
    const anonRaw = safeParseJson(st.getItem(ROAMING_ANON_STORAGE_KEY), null);
    if (anonRaw) {
      localState = mergeUserRoamingStates(localState, anonRaw);
      try {
        st.removeItem(ROAMING_ANON_STORAGE_KEY);
      } catch (_e) {}
    }
  }
  const wasDirty = isLocalUserRoamingStateDirty({ email, storage: st });

  const pullRes = await fetchCloudUserRoamingState({ webhookUrl, session, fetchImpl });
  if (!pullRes.ok) {
    const savedOffline = saveLocalUserRoamingState(localState, {
      email,
      storage: st,
      markDirty: wasDirty,
      mirrorLegacy: true,
    });
    return {
      ok: false,
      offlineOrStub: true,
      state: savedOffline,
      changedFromLocal: false,
    };
  }

  const remoteState = pullRes.userState || createEmptyRoamingState();
  const merged = mergeUserRoamingStates(localState, remoteState);
  const changedFromLocal = !areRoamingStatesEquivalent(localState, merged);
  const remoteNeedsUpdate =
    wasDirty ||
    (!pullRes.hasRemoteRecord && Boolean(merged.updatedAt)) ||
    !areRoamingStatesEquivalent(remoteState, merged);

  let finalState = merged;
  let pushed = false;
  if (remoteNeedsUpdate && merged.updatedAt) {
    const pushRes = await pushCloudUserRoamingState({
      webhookUrl,
      session,
      state: merged,
      fetchImpl,
    });
    if (pushRes.ok) {
      finalState = mergeUserRoamingStates(merged, pushRes.userState);
      pushed = true;
    } else {
      saveLocalUserRoamingState(finalState, {
        email,
        storage: st,
        markDirty: true,
        mirrorLegacy: true,
      });
      return {
        ok: false,
        offlineOrStub: true,
        state: finalState,
        changedFromLocal,
      };
    }
  }

  const saved = saveLocalUserRoamingState(finalState, {
    email,
    storage: st,
    markDirty: false,
    mirrorLegacy: true,
  });
  return {
    ok: true,
    pushed,
    state: saved,
    changedFromLocal: !areRoamingStatesEquivalent(localState, saved),
  };
}
