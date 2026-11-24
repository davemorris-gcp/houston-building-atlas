/**
 * Shared Authentication, Role Isolation, JWT Expiry Preflight, Cross-Tab Broadcast,
 * and Role-Scoped Monitoring Cache Controller (`monitoringAuthClient.js`).
 *
 * Pure, DOM-guarded ES module imported by `plat-monitor.html`, `index.html`,
 * `curatedEdits.js`, `staffWorkbench.js`, and `app.js` so session parsing, `Staff` vs.
 * `Screener` capability checks, 1-hour OIDC token expiry recovery, and Sign Out cache
 * wiping live in a single Node-testable module.
 */

import {
  DEFAULT_SHEET_WEBHOOK_URL,
  clearLegacyMirrorOnSignOut,
  hydrateRoamingStateFromVerifyResponse,
  normalizeStaffEmail,
  pushCloudUserRoamingState,
} from "./userRoamingState.js?v=20261113c";
import { sanitizeMonitoringText } from "./parcelMonitoringModel.js?v=20261113c";

export const LS_ADMIN_SESSION_KEY = "ph_atlas_admin_session";
export const LS_AUTH_CACHE_PREFIX = "phpam:auth_cache:";
export const LS_CROSS_TAB_BROADCAST_KEY = "ph_atlas_auth_broadcast";
export const LS_CONFIGURED_GOOGLE_CLIENT_ID_KEY = "ph_atlas_google_client_id";

export const LEGACY_PRIVATE_STORAGE_KEYS = Object.freeze([
  "phpam:watchlist:v2",
  "phpam:history",
]);
export const LEGACY_PRIVATE_STORAGE_PREFIXES = Object.freeze(["phpam:cycle:"]);

const _pendingAuthRetryQueue = [];
const _retryCausalSeq = new WeakMap();
const _activeCoordinatorClearHooks = new Set();
let _nextCausalSeq = 1;
let _authRetryClearGen = 0;
let _gsiScriptPromise = null;

function getSafeStorage(kind = "local") {
  try {
    if (kind === "session" && typeof sessionStorage !== "undefined" && sessionStorage) {
      return sessionStorage;
    }
    if (kind === "local" && typeof localStorage !== "undefined" && localStorage) {
      return localStorage;
    }
  } catch (_e) {
    // Sandboxed iframe or private mode storage restriction
  }
  return null;
}

function listStorageKeys(storage) {
  if (!storage) return [];
  if (typeof storage.keys === "function") {
    try {
      const res = storage.keys();
      if (Array.isArray(res)) return res;
    } catch (_e) {}
  }
  const out = [];
  try {
    if (typeof storage.length === "number" && typeof storage.key === "function") {
      for (let i = 0; i < storage.length; i += 1) {
        const k = storage.key(i);
        if (k) out.push(k);
      }
      return out;
    }
    for (const k of Object.keys(storage)) {
      out.push(k);
    }
  } catch (_e2) {}
  return out;
}

/**
 * Decode a JWT payload client-side strictly for `exp` preflight UX checks (all trust and
 * authorization decisions are enforced server-side in Google Apps Script).
 */
export function decodeJwtPayloadUnsafe(idToken) {
  const tok = String(idToken || "").trim();
  const parts = tok.split(".");
  if (parts.length !== 3 || !parts[1]) return null;
  try {
    const b64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const pad = b64.length % 4 === 0 ? "" : "=".repeat(4 - (b64.length % 4));
    let jsonStr = "";
    if (typeof atob === "function") {
      const bin = atob(b64 + pad);
      const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
      jsonStr = new TextDecoder("utf-8").decode(bytes);
    } else if (typeof Buffer !== "undefined") {
      jsonStr = Buffer.from(b64 + pad, "base64").toString("utf8");
    }
    const obj = JSON.parse(jsonStr);
    return obj && typeof obj === "object" ? obj : null;
  } catch (_e) {
    return null;
  }
}

export function isSessionTokenExpired(session, nowMs = Date.now(), bufferSec = 45) {
  if (!session || typeof session !== "object" || !session.authorized) return true;
  if (session.adminPasskey) return false;
  const tok = String(session.googleIdToken || "").trim();
  if (!tok) return false;
  const decoded = decodeJwtPayloadUnsafe(tok);
  if (!decoded || !decoded.exp) return false;
  const expMs = Number(decoded.exp) * 1000;
  if (!Number.isFinite(expMs) || expMs <= 0) return false;
  return expMs <= nowMs + bufferSec * 1000;
}

export function normalizeMonitoringSession(raw) {
  if (!raw || typeof raw !== "object" || !raw.authorized) return null;
  const email = normalizeStaffEmail(raw.email || "");
  const rawRole = String(raw.role || "staff").trim().toLowerCase();
  const role = rawRole === "screener" ? "screener" : "staff";
  return {
    authorized: true,
    verifiedByWebhook: Boolean(raw.verifiedByWebhook),
    email: email || String(raw.email || "Preservation Staff").trim(),
    role,
    displayLabel: sanitizeMonitoringText(
      raw.displayLabel || (role === "screener" ? "Screener" : "Preservation Staff")
    ),
    assignedAreas: Array.isArray(raw.assignedAreas)
      ? raw.assignedAreas.map((a) => sanitizeMonitoringText(a)).filter(Boolean)
      : [],
    googleIdToken: String(raw.googleIdToken || "").trim(),
    adminPasskey: String(raw.adminPasskey || "").trim(),
    googleClientId: String(raw.googleClientId || "").trim(),
    supportsRoamingSync: raw.supportsRoamingSync !== false,
    supportsPhase1Monitoring: Boolean(raw.supportsPhase1Monitoring),
    unlockedAt: String(raw.unlockedAt || ""),
  };
}

function resolveMonitoringStores(opts = {}) {
  const hasExplicitStore = Boolean(
    opts && ("localStore" in opts || "sessionStore" in opts)
  );
  return {
    hasExplicitStore,
    localStore:
      opts && "localStore" in opts
        ? opts.localStore ?? null
        : hasExplicitStore
        ? null
        : getSafeStorage("local"),
    sessionStore:
      opts && "sessionStore" in opts
        ? opts.sessionStore ?? null
        : hasExplicitStore
        ? null
        : getSafeStorage("session"),
  };
}

function readSessionFromStore(store) {
  if (!store) return null;
  try {
    const rawStr = store.getItem(LS_ADMIN_SESSION_KEY);
    if (rawStr) {
      return normalizeMonitoringSession(JSON.parse(rawStr));
    }
  } catch (_e) {}
  return null;
}

export function readActiveMonitoringSession(opts = {}) {
  const { localStore, sessionStore } = resolveMonitoringStores(opts);
  for (const store of [sessionStore, localStore]) {
    const parsed = readSessionFromStore(store);
    if (parsed) return parsed;
  }
  return null;
}

export function getActiveMonitoringRole(opts = {}) {
  const sess = readActiveMonitoringSession(opts);
  if (!sess || !sess.authorized) return "public";
  return sess.role === "screener" ? "screener" : "staff";
}

export function canAccessStaffOnly(sessionOrOpts = null) {
  const sess =
    sessionOrOpts && typeof sessionOrOpts === "object" && "authorized" in sessionOrOpts
      ? normalizeMonitoringSession(sessionOrOpts)
      : readActiveMonitoringSession(sessionOrOpts || {});
  return Boolean(sess && sess.authorized && sess.role === "staff");
}

/**
 * Purge all `phpam:auth_cache:*` entries and legacy pre-Phase-1 un-namespaced private
 * Watchlist/Plat `localStorage` keys so signing out or switching `Staff -> Screener`
 * leaves zero private dossiers or `Staff_Only` notes on disk.
 */
export function purgePrivateMonitoringStorage(opts = {}) {
  const preserveEmail = opts?.preserveEmail || "";
  const { localStore } = resolveMonitoringStores(opts);
  if (!localStore) return;
  const keepCacheKey = preserveEmail
    ? `${LS_AUTH_CACHE_PREFIX}${normalizeStaffEmail(preserveEmail)}`
    : "";

  for (const k of LEGACY_PRIVATE_STORAGE_KEYS) {
    try {
      localStore.removeItem(k);
    } catch (_e) {}
  }

  for (const k of listStorageKeys(localStore)) {
    if (k.startsWith(LS_AUTH_CACHE_PREFIX) && k !== keepCacheKey) {
      try {
        localStore.removeItem(k);
      } catch (_e) {}
      continue;
    }
    if (LEGACY_PRIVATE_STORAGE_PREFIXES.some((pfx) => k.startsWith(pfx))) {
      try {
        localStore.removeItem(k);
      } catch (_e) {}
    }
  }
}

export function ensureCleanUnauthenticatedStorageOnBoot(opts = {}) {
  const { localStore, sessionStore } = resolveMonitoringStores(opts);
  const sess = readActiveMonitoringSession({ localStore, sessionStore });
  if (!sess) {
    purgePrivateMonitoringStorage({ localStore });
    return null;
  }
  // Even when signed in, scrub pre-Phase-1 un-namespaced keys so all private state lives strictly in role-checked cache
  purgePrivateMonitoringStorage({ localStore, preserveEmail: sess.email });
  return sess;
}

export function saveCachedMonitoringState(email, role, rawState = {}, opts = {}) {
  const { localStore } = resolveMonitoringStores(opts);
  const normEmail = normalizeStaffEmail(email);
  if (!localStore || !normEmail) return false;
  const cleanRole = role === "screener" ? "screener" : "staff";
  const safePayload = {
    schemaVersion: "1.0",
    role: cleanRole,
    cachedAt: new Date().toISOString(),
    areas: Array.isArray(rawState.areas) ? rawState.areas : [],
    parcels: Array.isArray(rawState.parcels) ? rawState.parcels : [],
    decisions: Array.isArray(rawState.decisions) ? rawState.decisions : [],
    events: Array.isArray(rawState.events) ? rawState.events : [],
    watchlistDossiers: Array.isArray(rawState.watchlistDossiers)
      ? rawState.watchlistDossiers
      : [],
    ...(cleanRole === "staff" && rawState.staffOnly
      ? {
          staffOnly: {
            parcelStrategy: Array.isArray(rawState.staffOnly.parcelStrategy)
              ? rawState.staffOnly.parcelStrategy
              : [],
            watchedEntities: Array.isArray(rawState.staffOnly.watchedEntities)
              ? rawState.staffOnly.watchedEntities
              : [],
          },
          pendingModerationCount: Number(rawState.pendingModerationCount) || 0,
        }
      : {}),
  };
  try {
    localStore.setItem(`${LS_AUTH_CACHE_PREFIX}${normEmail}`, JSON.stringify(safePayload));
    return true;
  } catch (_e) {
    return false;
  }
}

export function loadCachedMonitoringState(email, expectedRole, opts = {}) {
  const { localStore } = resolveMonitoringStores(opts);
  const normEmail = normalizeStaffEmail(email);
  if (!localStore || !normEmail) return null;
  try {
    const raw = localStore.getItem(`${LS_AUTH_CACHE_PREFIX}${normEmail}`);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return null;
    // Reject cached Staff state if user's role changed to Screener!
    if (expectedRole === "screener" && parsed.role !== "screener") {
      localStore.removeItem(`${LS_AUTH_CACHE_PREFIX}${normEmail}`);
      return null;
    }
    if (expectedRole === "screener") {
      delete parsed.staffOnly;
      delete parsed.pendingModerationCount;
    }
    return parsed;
  } catch (_e) {
    return null;
  }
}

function broadcastAuthEvent(eventType, meta = {}, localStore = null) {
  if (!localStore) return;
  try {
    localStore.setItem(
      LS_CROSS_TAB_BROADCAST_KEY,
      JSON.stringify({
        eventType,
        role: meta.role || "public",
        email: meta.email || "",
        at: Date.now(),
      })
    );
  } catch (_e) {}
}

export function signOutMonitoringSession(opts = {}) {
  const { localStore, sessionStore } = resolveMonitoringStores(opts);
  if (sessionStore) {
    try {
      sessionStore.removeItem(LS_ADMIN_SESSION_KEY);
    } catch (_e) {}
  }
  if (localStore) {
    try {
      localStore.removeItem(LS_ADMIN_SESSION_KEY);
    } catch (_e) {}
  }
  purgePrivateMonitoringStorage({ localStore });
  if (localStore) {
    clearLegacyMirrorOnSignOut(localStore);
  }
  clearPendingAuthRetries();
  broadcastAuthEvent("sign_out", { role: "public", email: "" }, localStore);
}

export async function verifyAndSignInMonitoring(opts = {}) {
  const {
    webhookUrl = DEFAULT_SHEET_WEBHOOK_URL,
    googleIdToken = "",
    email = "",
    passkey = "",
    fetchImpl = null,
  } = opts || {};
  const { localStore, sessionStore } = resolveMonitoringStores(opts);
  const token = String(googleIdToken || "").trim();
  const cleanEmail = String(email || "").trim();
  const cleanPasskey = String(passkey || "").trim();

  if (!token && (!cleanEmail || !cleanPasskey)) {
    return {
      ok: false,
      error: "Sign in with Google or enter both your authorized email and sync passkey.",
    };
  }

  const prevSession = readActiveMonitoringSession({ localStore, sessionStore });
  const doFetch = fetchImpl || (typeof fetch === "function" ? fetch : null);
  const targetUrl = String(webhookUrl || "").trim();

  let verifyData = null;
  let resolvedEmail = normalizeStaffEmail(cleanEmail) || cleanEmail;
  let resolvedRole = "staff";
  let verifiedByWebhook = false;

  if (targetUrl && doFetch) {
    try {
      const res = await doFetch(targetUrl, {
        method: "POST",
        headers: { "Content-Type": "text/plain;charset=utf-8" },
        body: JSON.stringify({
          action: "verify_admin",
          google_id_token: token,
          admin_email: cleanEmail,
          admin_passkey: cleanPasskey,
        }),
      });
      if (!res || !res.ok) {
        return {
          ok: false,
          error: `Verification service HTTP ${res ? res.status : "network_error"}`,
        };
      }
      verifyData = await res.json();
      if (!verifyData || !verifyData.authorized) {
        return {
          ok: false,
          error:
            (verifyData && verifyData.error) ||
            "Access denied: Google account or passkey is not authorized.",
        };
      }
      verifiedByWebhook = true;
      resolvedEmail = normalizeStaffEmail(verifyData.email || cleanEmail) || cleanEmail;
      resolvedRole = String(verifyData.role || "staff").toLowerCase() === "screener" ? "screener" : "staff";
      if (verifyData.googleClientId && localStore) {
        try {
          localStore.setItem(LS_CONFIGURED_GOOGLE_CLIENT_ID_KEY, String(verifyData.googleClientId));
        } catch (_e) {}
      }
    } catch (err) {
      return {
        ok: false,
        error: `Could not reach Google Workspace verification service: ${err?.message || err}`,
      };
    }
  }

  const switchedAccountOrRole = Boolean(
    prevSession &&
      (normalizeStaffEmail(prevSession.email) !== normalizeStaffEmail(resolvedEmail) ||
        prevSession.role !== resolvedRole)
  );
  if (switchedAccountOrRole) {
    clearPendingAuthRetries();
    purgePrivateMonitoringStorage({ localStore });
    if (localStore) {
      clearLegacyMirrorOnSignOut(localStore);
    }
  } else {
    if (!prevSession) {
      clearPendingAuthRetries();
    }
    purgePrivateMonitoringStorage({ preserveEmail: resolvedEmail, localStore });
  }

  const session = normalizeMonitoringSession({
    authorized: true,
    verifiedByWebhook,
    email: resolvedEmail,
    role: resolvedRole,
    displayLabel:
      verifyData?.displayLabel || (resolvedRole === "screener" ? "Screener" : "Preservation Staff"),
    assignedAreas: verifyData?.assignedAreas || [],
    googleIdToken: token,
    adminPasskey: cleanPasskey,
    googleClientId: verifyData?.googleClientId || "",
    supportsRoamingSync: verifyData?.supportsRoamingSync !== false,
    supportsPhase1Monitoring: Boolean(verifyData?.supportsPhase1Monitoring),
    unlockedAt: new Date().toISOString(),
  });

  if (sessionStore) {
    try {
      sessionStore.setItem(LS_ADMIN_SESSION_KEY, JSON.stringify(session));
    } catch (_e) {}
  }
  if (localStore) {
    try {
      localStore.setItem(LS_ADMIN_SESSION_KEY, JSON.stringify(session));
    } catch (_e) {}
  }

  let roamingHydration = null;
  if (verifiedByWebhook && verifyData && localStore) {
    roamingHydration = hydrateRoamingStateFromVerifyResponse({
      verifyData,
      email: resolvedEmail,
      storage: localStore,
    });
    if (roamingHydration.supportsRoamingSync && roamingHydration.needsPush && targetUrl && doFetch) {
      pushCloudUserRoamingState({
        webhookUrl: targetUrl,
        session,
        state: roamingHydration.state,
        fetchImpl: doFetch,
      }).catch(() => {});
    }
  }

  broadcastAuthEvent("sign_in", { role: session.role, email: session.email }, localStore);
  return {
    ok: true,
    session,
    switchedAccountOrRole,
    roamingHydration,
  };
}

function isRetryOwnedBySession(item, activeSession) {
  if (!activeSession) return false;
  const activeEmail = normalizeStaffEmail(activeSession.email || "");
  const activeRole = activeSession.role || "";
  if (item.ownerEmail && item.ownerEmail !== activeEmail) return false;
  if (item.ownerRole && item.ownerRole !== activeRole) return false;
  return true;
}

function cancelQueuedRetryItem(item) {
  if (typeof item?.retryFn?.cancelQueuedRetry === "function") {
    try {
      item.retryFn.cancelQueuedRetry();
    } catch (_e) {
      // Isolate per-item cancellation callback errors so sibling tickets and sign-out complete cleanly.
    }
  }
}

function insertRetryOrdered(entry) {
  const existingIdx = _pendingAuthRetryQueue.findIndex((x) => x.retryFn === entry.retryFn);
  if (existingIdx !== -1) {
    _pendingAuthRetryQueue.splice(existingIdx, 1);
  }
  const insertAt = _pendingAuthRetryQueue.findIndex((x) => x.causalSeq > entry.causalSeq);
  if (insertAt === -1) {
    _pendingAuthRetryQueue.push(entry);
  } else {
    _pendingAuthRetryQueue.splice(insertAt, 0, entry);
  }
}

export function clearPendingAuthRetries() {
  _authRetryClearGen += 1;
  for (const cancelActive of Array.from(_activeCoordinatorClearHooks)) {
    try {
      cancelActive();
    } catch (_e) {}
  }
  const discarded = _pendingAuthRetryQueue.splice(0, _pendingAuthRetryQueue.length);
  for (const item of discarded) {
    cancelQueuedRetryItem(item);
  }
}

/**
 * Queue a deferred RPC closure for replay after re-authentication. Callers holding external
 * concurrency tickets may attach `retryFn.cancelQueuedRetry = () => void` to release them on eviction.
 */
export function queuePendingAuthRetry(retryFn, description = "", session = null, opts = {}) {
  if (typeof retryFn === "function") {
    const owner = session || readActiveMonitoringSession(opts);
    let causalSeq = _retryCausalSeq.get(retryFn);
    if (!causalSeq) {
      causalSeq = _nextCausalSeq++;
      _retryCausalSeq.set(retryFn, causalSeq);
    }
    insertRetryOrdered({
      retryFn,
      description,
      causalSeq,
      clearGen: _authRetryClearGen,
      ownerEmail: normalizeStaffEmail(owner?.email || ""),
      ownerRole: owner?.role || "",
      queuedAt: Date.now(),
    });
  }
  return _pendingAuthRetryQueue.length;
}

export function getPendingAuthRetryCount() {
  return _pendingAuthRetryQueue.length;
}

export async function replayPendingAuthRetries(opts = {}) {
  const { hasExplicitStore, localStore, sessionStore } = resolveMonitoringStores(opts);
  const explicitSession = opts?.session || null;
  const hasSessionContext = Boolean(explicitSession || localStore || sessionStore || hasExplicitStore);
  const active =
    explicitSession || readActiveMonitoringSession({ localStore, sessionStore });
  if (hasSessionContext && (!active || !active.authorized)) {
    clearPendingAuthRetries();
    return [];
  }
  const capturedClearGen = _authRetryClearGen;
  const pending = _pendingAuthRetryQueue.splice(0, _pendingAuthRetryQueue.length);
  const results = [];
  const cancelInvalidatedReplayTail = (currentItem, nextIdx) => {
    if (currentItem) {
      cancelQueuedRetryItem(currentItem);
    }
    for (let j = nextIdx; j < pending.length; j += 1) {
      cancelQueuedRetryItem(pending[j]);
    }
    for (let k = _pendingAuthRetryQueue.length - 1; k >= 0; k -= 1) {
      const cand = _pendingAuthRetryQueue[k];
      if (cand.clearGen <= capturedClearGen || cand.retryFn === currentItem?.retryFn) {
        const [stale] = _pendingAuthRetryQueue.splice(k, 1);
        cancelQueuedRetryItem(stale);
      }
    }
  };
  for (let i = 0; i < pending.length; i += 1) {
    const item = pending[i];
    if (_authRetryClearGen !== capturedClearGen) {
      cancelInvalidatedReplayTail(null, i);
      break;
    }
    if (active && !isRetryOwnedBySession(item, active)) {
      cancelQueuedRetryItem(item);
      continue;
    }
    try {
      const result = await item.retryFn();
      if (_authRetryClearGen !== capturedClearGen) {
        cancelInvalidatedReplayTail(item, i + 1);
        break;
      }
      results.push({ ok: true, result, description: item.description });
      if (result && result.queuedForRetry) {
        for (let j = i + 1; j < pending.length; j += 1) {
          const rem = pending[j];
          if (active && !isRetryOwnedBySession(rem, active)) {
            cancelQueuedRetryItem(rem);
          } else {
            insertRetryOrdered(rem);
          }
        }
        break;
      }
    } catch (err) {
      if (_authRetryClearGen !== capturedClearGen) {
        cancelInvalidatedReplayTail(item, i + 1);
        break;
      }
      results.push({ ok: false, error: err?.message || String(err), description: item.description });
    }
  }
  return results;
}

/**
 * Execute an authenticated Apps Script `doPost` RPC with JWT `exp` preflight detection
 * so 1-hour Google ID token expiry pauses cleanly for 1-click re-auth without losing dirty edits.
 * Optional `retryFn` may expose `retryFn.cancelQueuedRetry = () => void` for eviction cleanup.
 */
export async function callMonitoringRpc(opts = {}) {
  const {
    webhookUrl = DEFAULT_SHEET_WEBHOOK_URL,
    action,
    body = {},
    session = null,
    fetchImpl = null,
    onTokenExpired = null,
    retryFn = null,
  } = opts || {};
  const activeSession = session || readActiveMonitoringSession(opts);
  if (!activeSession || !activeSession.authorized) {
    return { ok: false, authorized: false, error: "Sign-in required." };
  }

  if (isSessionTokenExpired(activeSession)) {
    if (typeof retryFn === "function") {
      queuePendingAuthRetry(retryFn, action, activeSession, opts);
    }
    if (typeof onTokenExpired === "function") {
      onTokenExpired({ action, session: activeSession });
    }
    return {
      ok: false,
      authorized: false,
      tokenExpired: true,
      queuedForRetry: typeof retryFn === "function",
      error: "Google Sign-In session expired; sign in again to save your pending changes.",
    };
  }

  const doFetch = fetchImpl || (typeof fetch === "function" ? fetch : null);
  const targetUrl = String(webhookUrl || "").trim();
  if (!targetUrl || !doFetch) {
    return { ok: false, offline: true, error: "Cloud monitoring endpoint unavailable." };
  }

  const reqPayload = {
    ...body,
    action,
    admin_email: activeSession.email || "",
    admin_passkey: activeSession.adminPasskey || "",
    google_id_token: activeSession.googleIdToken || "",
  };

  const capturedClearGen = _authRetryClearGen;
  try {
    const res = await doFetch(targetUrl, {
      method: "POST",
      headers: { "Content-Type": "text/plain;charset=utf-8" },
      body: JSON.stringify(reqPayload),
    });
    if (!res || !res.ok) {
      return { ok: false, error: `Monitoring RPC HTTP ${res ? res.status : "network_error"}` };
    }
    const data = await res.json();
    if (
      data &&
      data.authorized === false &&
      /expired|invalid.*token/i.test(String(data.error || ""))
    ) {
      const latestSession = session || readActiveMonitoringSession(opts);
      const stillOwned = Boolean(
        _authRetryClearGen === capturedClearGen &&
          latestSession &&
          latestSession.authorized &&
          isRetryOwnedBySession(
            {
              ownerEmail: normalizeStaffEmail(activeSession.email || ""),
              ownerRole: activeSession.role || "",
            },
            latestSession
          )
      );
      if (!stillOwned) {
        return {
          ...data,
          ok: false,
          tokenExpired: true,
          queuedForRetry: false,
          cancelled: true,
        };
      }
      if (typeof retryFn === "function") {
        queuePendingAuthRetry(retryFn, action, activeSession, opts);
      }
      if (typeof onTokenExpired === "function") {
        onTokenExpired({ action, session: activeSession });
      }
      return {
        ...data,
        ok: false,
        tokenExpired: true,
        queuedForRetry: typeof retryFn === "function",
      };
    }
    return data;
  } catch (err) {
    return { ok: false, error: err?.message || String(err) };
  }
}

export async function pullCloudMonitoringState(opts = {}) {
  const {
    webhookUrl = DEFAULT_SHEET_WEBHOOK_URL,
    session = null,
    fetchImpl = null,
    onTokenExpired = null,
  } = opts || {};
  const activeSession = session || readActiveMonitoringSession(opts);
  if (!activeSession || !activeSession.authorized) {
    return { ok: false, authorized: false, signedOut: true };
  }
  const resp = await callMonitoringRpc({
    ...opts,
    webhookUrl,
    action: "pull_monitoring_state",
    session: activeSession,
    fetchImpl,
    onTokenExpired,
  });
  if (resp && resp.ok) {
    saveCachedMonitoringState(activeSession.email, resp.role || activeSession.role, resp, opts);
  }
  return resp;
}

/**
 * Subscribe to cross-tab `storage` events so Sign Out or `Staff -> Screener` switches
 * in a sibling browser tab immediately unmount private DOM nodes and memory caches.
 */
export function subscribeCrossTabAuthChanges(
  onAuthChanged,
  targetWindow = null,
  opts = {}
) {
  const win = targetWindow || (typeof window !== "undefined" ? window : null);
  if (!win || typeof win.addEventListener !== "function") {
    return () => {};
  }
  const { localStore, sessionStore } = resolveMonitoringStores(opts);
  const initialSession = readActiveMonitoringSession({ localStore, sessionStore });
  let lastEmail = normalizeStaffEmail(initialSession?.email || "");
  let lastRole = initialSession?.role || "";
  const handler = (ev) => {
    if (!ev) return;
    if (ev.key === LS_ADMIN_SESSION_KEY || ev.key === LS_CROSS_TAB_BROADCAST_KEY || ev.key === null) {
      if (localStore && sessionStore) {
        const tabSess = readSessionFromStore(sessionStore);
        const localSess = readSessionFromStore(localStore);
        const tabEmail = normalizeStaffEmail(tabSess?.email || "");
        const localEmail = normalizeStaffEmail(localSess?.email || "");
        const tabMirroredPrior =
          Boolean(tabSess) && tabEmail === lastEmail && (tabSess.role || "") === lastRole;
        if (
          tabMirroredPrior &&
          (!localSess || localEmail !== tabEmail || (localSess.role || "") !== (tabSess.role || ""))
        ) {
          try {
            sessionStore.removeItem(LS_ADMIN_SESSION_KEY);
          } catch (_e) {}
        }
      }
      const currentSession = readActiveMonitoringSession({ localStore, sessionStore });
      const nextEmail = normalizeStaffEmail(currentSession?.email || "");
      const nextRole = currentSession?.role || "";
      if (!currentSession) {
        clearPendingAuthRetries();
        purgePrivateMonitoringStorage({ localStore });
      } else {
        if (lastEmail !== nextEmail || lastRole !== nextRole) {
          for (const cancelActive of Array.from(_activeCoordinatorClearHooks)) {
            try {
              cancelActive();
            } catch (_e) {}
          }
        }
        for (let i = _pendingAuthRetryQueue.length - 1; i >= 0; i--) {
          if (!isRetryOwnedBySession(_pendingAuthRetryQueue[i], currentSession)) {
            const [removed] = _pendingAuthRetryQueue.splice(i, 1);
            cancelQueuedRetryItem(removed);
          }
        }
      }
      lastEmail = nextEmail;
      lastRole = nextRole;
      if (typeof onAuthChanged === "function") {
        onAuthChanged({
          session: currentSession,
          role: currentSession ? currentSession.role : "public",
          signedOut: !currentSession,
        });
      }
    }
  };
  win.addEventListener("storage", handler);
  return () => {
    try {
      win.removeEventListener("storage", handler);
    } catch (_e) {}
  };
}

export function resolveConfiguredGoogleClientId(opts = {}) {
  const explicitClientId = opts?.explicitClientId || "";
  const { localStore } = resolveMonitoringStores(opts);
  if (explicitClientId && String(explicitClientId).trim()) {
    return String(explicitClientId).trim();
  }
  if (typeof document !== "undefined" && document?.querySelector) {
    const meta = document.querySelector('meta[name="atlas-google-client-id"]');
    const content = String(meta?.getAttribute("content") || "").trim();
    if (content) return content;
  }
  if (localStore) {
    try {
      const saved = String(localStore.getItem(LS_CONFIGURED_GOOGLE_CLIENT_ID_KEY) || "").trim();
      if (saved) return saved;
    } catch (_e) {}
  }
  return "";
}

export async function mountGoogleSignInButton(containerEl, opts = {}) {
  const { clientId = "", onCredential = null } = opts || {};
  const resolvedClientId = resolveConfiguredGoogleClientId({ ...opts, explicitClientId: clientId });
  if (
    !containerEl ||
    !resolvedClientId ||
    typeof window === "undefined" ||
    typeof document === "undefined"
  ) {
    return { mounted: false, reason: "no_client_id" };
  }
  if (!window.google?.accounts?.id) {
    if (!_gsiScriptPromise) {
      _gsiScriptPromise = new Promise((resolve) => {
        const s = document.createElement("script");
        s.src = "https://accounts.google.com/gsi/client";
        s.async = true;
        s.defer = true;
        s.onload = () => resolve(true);
        s.onerror = () => resolve(false);
        document.head.appendChild(s);
      });
    }
    const ok = await _gsiScriptPromise;
    if (!ok || !window.google?.accounts?.id) {
      return { mounted: false, reason: "gsi_unavailable" };
    }
  }
  window.google.accounts.id.initialize({
    client_id: resolvedClientId,
    callback: (resp) => {
      if (resp?.credential && typeof onCredential === "function") {
        onCredential(String(resp.credential));
      }
    },
  });
  containerEl.innerHTML = "";
  window.google.accounts.id.renderButton(containerEl, {
    theme: "outline",
    size: "large",
    text: "signin_with",
    shape: "rectangular",
    width: 280,
  });
  return { mounted: true };
}

/**
 * Pure, timer-injectable client optimistic-concurrency coordinator for `upsert_parcel_decision`
 * RPCs. Coalesces rapid debounced edits per parcel, keeps pending batches active across
 * in-flight requests so overlapping background pulls cannot clobber optimistic state or
 * regress server-confirmed `decided_at` timestamps, and resolves `expected_updated_at`
 * dynamically inside `sendRpc` so replayed expired-token retries chain cleanly.
 */
export function createParcelSyncCoordinator({
  debounceMs = 650,
  sendDecisionRpc,
  onSuccess = null,
  onConflict = null,
  scheduleTimer = (fn, ms) => setTimeout(fn, ms),
  cancelTimer = (id) => clearTimeout(id),
} = {}) {
  const recordsByParcelId = new Map();
  const promotedTargetBySourcePid = new Map();
  const activeBatchTickets = new Set();
  let generation = 0;

  function getOrCreateRecord(pid) {
    let rec = recordsByParcelId.get(pid);
    if (!rec) {
      rec = {
        hasServerTs: false,
        serverTs: "",
        pendingBatch: null,
        timerId: null,
      };
      recordsByParcelId.set(pid, rec);
    }
    return rec;
  }

  function resolveLatestTargetPid(startPid) {
    let cur = String(startPid || "").trim();
    const seen = new Set();
    while (cur && promotedTargetBySourcePid.has(cur) && !seen.has(cur)) {
      seen.add(cur);
      cur = promotedTargetBySourcePid.get(cur);
    }
    return cur;
  }

  function getPromotedTarget(parcelId) {
    return resolveLatestTargetPid(parcelId);
  }

  function hasActiveTicket(pid) {
    for (const t of activeBatchTickets) {
      if (t.pid === pid) return true;
    }
    return false;
  }

  function hasOwnActive(pid) {
    const rec = recordsByParcelId.get(pid);
    return Boolean((rec && rec.pendingBatch) || hasActiveTicket(pid));
  }

  function getBlockingTicketPromise(pid, maxSeq = Infinity) {
    const targetPid = resolveLatestTargetPid(pid) || pid;
    for (const t of activeBatchTickets) {
      if (t.seq < maxSeq && (resolveLatestTargetPid(t.pid) || t.pid) === targetPid) {
        return t.promise;
      }
    }
    return null;
  }

  function hasNewerActiveTicket(pid, minSeq) {
    const targetPid = resolveLatestTargetPid(pid) || pid;
    for (const t of activeBatchTickets) {
      if (t.seq > minSeq && (resolveLatestTargetPid(t.pid) || t.pid) === targetPid) {
        return true;
      }
    }
    return false;
  }

  function hasAnyScheduledOrActiveWork() {
    if (activeBatchTickets.size > 0) return true;
    for (const rec of recordsByParcelId.values()) {
      if (rec.timerId !== null || rec.pendingBatch !== null) return true;
    }
    return false;
  }

  function syncClearHookRegistration() {
    if (hasAnyScheduledOrActiveWork()) {
      _activeCoordinatorClearHooks.add(cancelActiveWork);
    } else {
      _activeCoordinatorClearHooks.delete(cancelActiveWork);
    }
  }

  function cancelActiveWork() {
    for (const rec of recordsByParcelId.values()) {
      if (rec.timerId !== null) {
        cancelTimer(rec.timerId);
        rec.timerId = null;
      }
      rec.pendingBatch = null;
    }
    const openTickets = Array.from(activeBatchTickets);
    activeBatchTickets.clear();
    for (const t of openTickets) {
      t.markCancelled();
    }
    for (const t of openTickets) {
      t.resolve();
    }
    _activeCoordinatorClearHooks.delete(cancelActiveWork);
  }

  function clearAll() {
    generation += 1;
    cancelActiveWork();
    recordsByParcelId.clear();
    promotedTargetBySourcePid.clear();
  }

  function hasPendingOrInFlight(parcelId) {
    const pid = String(parcelId || "").trim();
    if (!pid) return false;
    const targetPid = resolveLatestTargetPid(pid) || pid;
    if (hasOwnActive(pid) || hasOwnActive(targetPid)) return true;
    for (const srcPid of promotedTargetBySourcePid.keys()) {
      if ((resolveLatestTargetPid(srcPid) || srcPid) === targetPid && hasOwnActive(srcPid)) {
        return true;
      }
    }
    return false;
  }

  function getServerTimestamp(parcelId) {
    const pid = String(parcelId || "").trim();
    const rec = recordsByParcelId.get(pid);
    return rec && rec.hasServerTs ? rec.serverTs : "";
  }

  function recordHydratedTimestamp(parcelId, decidedAt, { fromCloud = false } = {}) {
    const pid = String(parcelId || "").trim();
    if (!pid) return;
    const incTs = String(decidedAt || "").trim();
    const rec = getOrCreateRecord(pid);
    if (fromCloud) {
      if (!hasPendingOrInFlight(pid)) {
        if (!rec.hasServerTs || !rec.serverTs || (incTs && incTs >= rec.serverTs)) {
          rec.hasServerTs = true;
          rec.serverTs = incTs;
        }
      }
    } else if (!rec.hasServerTs && incTs) {
      rec.hasServerTs = true;
      rec.serverTs = incTs;
    }
  }

  function captureBaselineExpectedTs(parcelId, fallbackDecidedAt = "") {
    const pid = String(parcelId || "").trim();
    if (!pid) return "";
    const rec = getOrCreateRecord(pid);
    if (!rec.hasServerTs) {
      rec.hasServerTs = true;
      rec.serverTs = String(fallbackDecidedAt || "").trim();
    }
    return rec.serverTs;
  }

  function promoteParcelKey(fromParcelId, toParcelId, fallbackDecidedAt = "") {
    const fromPid = String(fromParcelId || "").trim();
    const toPid = String(toParcelId || "").trim();
    if (!fromPid || !toPid || fromPid === toPid) {
      return captureBaselineExpectedTs(toPid || fromPid, fallbackDecidedAt);
    }
    const baseTs = captureBaselineExpectedTs(fromPid, fallbackDecidedAt);
    const fromRec = getOrCreateRecord(fromPid);
    const toRec = getOrCreateRecord(toPid);
    if (!toRec.hasServerTs) {
      toRec.hasServerTs = true;
      toRec.serverTs = baseTs;
    }
    promotedTargetBySourcePid.delete(toPid);
    promotedTargetBySourcePid.set(fromPid, toPid);
    if (fromRec.timerId !== null) {
      cancelTimer(fromRec.timerId);
      fromRec.timerId = null;
    }
    if (fromRec.pendingBatch) {
      const priorFrom = fromRec.pendingBatch;
      fromRec.pendingBatch = null;
      const existingTo = toRec.pendingBatch;
      const rootReplacePid =
        priorFrom.rootReplacePid || priorFrom.patchPayload?.replace_parcel_id || fromPid;
      const nextPatch = {
        ...priorFrom.patchPayload,
        ...(existingTo ? existingTo.patchPayload : {}),
        replace_parcel_id: rootReplacePid,
      };
      if (nextPatch.replace_parcel_id === toPid) {
        delete nextPatch.replace_parcel_id;
      }
      toRec.pendingBatch = {
        baselineExpectedTs: existingTo ? existingTo.baselineExpectedTs : priorFrom.baselineExpectedTs,
        rootReplacePid,
        clearGen: existingTo ? existingTo.clearGen : priorFrom.clearGen,
        patchPayload: nextPatch,
      };
    }
    syncClearHookRegistration();
    return toRec.serverTs;
  }

  function enqueue(parcelId, patchPayload = {}, baselineExpectedTs = "") {
    const pid = String(parcelId || "").trim();
    if (!pid || typeof sendDecisionRpc !== "function") return;
    const rec = getOrCreateRecord(pid);
    const prev = rec.pendingBatch;
    const mergedPatch = prev ? { ...prev.patchPayload, ...patchPayload } : { ...patchPayload };
    const rootReplacePid = prev
      ? prev.rootReplacePid || prev.patchPayload?.replace_parcel_id || ""
      : String(patchPayload.replace_parcel_id || "").trim();
    // Preserve the pre-debounce server lookup PK across chained <650ms CAMPUS:<SLUG> renames.
    if (rootReplacePid) {
      mergedPatch.replace_parcel_id = rootReplacePid;
    }
    if (mergedPatch.replace_parcel_id === pid) {
      delete mergedPatch.replace_parcel_id;
    }
    const batchClearGen = _authRetryClearGen;
    rec.pendingBatch = {
      baselineExpectedTs: prev ? prev.baselineExpectedTs : String(baselineExpectedTs || "").trim(),
      rootReplacePid,
      clearGen: batchClearGen,
      patchPayload: mergedPatch,
    };
    if (rec.timerId !== null) {
      cancelTimer(rec.timerId);
      rec.timerId = null;
    }
    const capturedGen = generation;
    rec.timerId = scheduleTimer(async () => {
      if (capturedGen !== generation || batchClearGen !== _authRetryClearGen) {
        syncClearHookRegistration();
        return { ok: false, cancelled: true };
      }
      rec.timerId = null;
      const batch = rec.pendingBatch;
      if (!batch || batch.clearGen !== _authRetryClearGen) {
        syncClearHookRegistration();
        return { ok: false, cancelled: true };
      }
      // Atomically transition pid from pendingBatch -> activeBatchTickets with zero microtask gap
      rec.pendingBatch = null;
      let retryCancelled = false;
      let settleBatchTicket;
      const batchTicket = {
        seq: _nextCausalSeq++,
        pid,
        promise: new Promise((resolve) => {
          settleBatchTicket = resolve;
        }),
        resolve: () => settleBatchTicket(),
        markCancelled: () => {
          retryCancelled = true;
        },
        finish: () => {
          if (activeBatchTickets.delete(batchTicket)) {
            syncClearHookRegistration();
            batchTicket.resolve();
          }
        },
      };
      activeBatchTickets.add(batchTicket);
      syncClearHookRegistration();
      const finishBatchTicket = () => batchTicket.finish();

      const sendRpc = async () => {
        if (capturedGen !== generation || retryCancelled || batch.clearGen !== _authRetryClearGen) {
          finishBatchTicket();
          return { ok: false, cancelled: true };
        }
        for (
          let blocking = getBlockingTicketPromise(pid, batchTicket.seq);
          blocking;
          blocking = getBlockingTicketPromise(pid, batchTicket.seq)
        ) {
          try {
            await blocking;
          } catch (_e) {}
          if (capturedGen !== generation || retryCancelled || batch.clearGen !== _authRetryClearGen) {
            finishBatchTicket();
            return { ok: false, cancelled: true };
          }
        }
        const expectedTs = rec.hasServerTs ? rec.serverTs : batch.baselineExpectedTs;
        let res;
        try {
          res = await sendDecisionRpc({
            parcelId: pid,
            expectedUpdatedAt: expectedTs,
            patch: batch.patchPayload,
            retryFn: sendRpc,
          });
          if (capturedGen !== generation || retryCancelled || batch.clearGen !== _authRetryClearGen) {
            return res;
          }
          if (res && res.queuedForRetry) {
            return res;
          }
          if (res && res.ok && res.decision && res.decision.parcel_id) {
            const srvPid = String(res.decision.parcel_id).trim();
            const srvTs = String(res.decision.decided_at || "").trim();
            const lineageRoot = resolveLatestTargetPid(pid) || pid;
            const targetKeys = new Set([
              srvPid,
              pid,
              resolveLatestTargetPid(srvPid),
              lineageRoot,
            ]);
            for (const candPid of recordsByParcelId.keys()) {
              if ((resolveLatestTargetPid(candPid) || candPid) === lineageRoot) {
                targetKeys.add(candPid);
              }
            }
            for (const keyPid of targetKeys) {
              if (!keyPid) continue;
              const targetRec = getOrCreateRecord(keyPid);
              if (!targetRec.hasServerTs || !targetRec.serverTs || srvTs >= targetRec.serverTs) {
                targetRec.hasServerTs = true;
                targetRec.serverTs = srvTs;
              }
            }
            const latestTargetPid = resolveLatestTargetPid(pid);
            const isSuperseded = Boolean(latestTargetPid && latestTargetPid !== pid);
            const ownFollowUpQueued = Boolean(
              rec.pendingBatch || hasNewerActiveTicket(pid, batchTicket.seq)
            );
            if (typeof onSuccess === "function") {
              onSuccess(res, {
                hasNewerPendingEdit: isSuperseded || ownFollowUpQueued,
                supersededByParcelId: isSuperseded ? latestTargetPid : "",
              });
            }
          } else if (res && res.conflict) {
            if (res.currentDecision && res.currentDecision.decided_at) {
              rec.hasServerTs = true;
              rec.serverTs = String(res.currentDecision.decided_at).trim();
            } else {
              rec.hasServerTs = false;
              rec.serverTs = "";
            }
            if (typeof onConflict === "function") {
              onConflict(res);
            }
          }
          return res;
        } finally {
          if (
            !(
              res &&
              res.queuedForRetry &&
              capturedGen === generation &&
              !retryCancelled &&
              batch.clearGen === _authRetryClearGen
            )
          ) {
            finishBatchTicket();
          }
        }
      };
      _retryCausalSeq.set(sendRpc, batchTicket.seq);
      sendRpc.cancelQueuedRetry = () => {
        const rootTarget = resolveLatestTargetPid(pid) || pid;
        for (const [recPid, candRec] of recordsByParcelId.entries()) {
          if ((resolveLatestTargetPid(recPid) || recPid) === rootTarget) {
            if (candRec.timerId !== null) {
              cancelTimer(candRec.timerId);
              candRec.timerId = null;
            }
            candRec.pendingBatch = null;
          }
        }
        const toCancel = [];
        for (const t of activeBatchTickets) {
          const tRoot = resolveLatestTargetPid(t.pid) || t.pid;
          if (t === batchTicket || (t.seq > batchTicket.seq && tRoot === rootTarget)) {
            toCancel.push(t);
          }
        }
        for (const t of toCancel) {
          t.markCancelled();
        }
        for (const t of toCancel) {
          t.finish();
        }
        syncClearHookRegistration();
      };

      return await sendRpc();
    }, debounceMs);
    syncClearHookRegistration();
  }

  return {
    clearAll,
    getPromotedTarget,
    hasPendingOrInFlight,
    getServerTimestamp,
    recordHydratedTimestamp,
    captureBaselineExpectedTs,
    promoteParcelKey,
    enqueue,
  };
}
