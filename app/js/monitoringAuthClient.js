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
const _activeCrossTabSyncHooks = new Set();
let _nextCausalSeq = 1;
// Increments only on full retry-queue wipes (sign-out, clearPendingAuthRetries, account/role switch).
let _authRetryClearGen = 0;
// Increments on every _authRetryClearGen bump AND on cross-tab active identity/role transitions,
// strictly dominating _authRetryClearGen so in-flight operations can guard against a single counter.
let _authSessionEpoch = 0;
let _gsiScriptPromise = null;

function notifyCoordinatorClearHooks() {
  for (const cancelActive of Array.from(_activeCoordinatorClearHooks)) {
    try {
      cancelActive();
    } catch (_e) {}
  }
}

function notifySameTabSyncHooks(localStore, sessionStore) {
  for (const syncHook of Array.from(_activeCrossTabSyncHooks)) {
    try {
      syncHook(localStore, sessionStore);
    } catch (_e) {}
  }
}

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

function getSessionIdentityKey(session) {
  const norm = normalizeMonitoringSession(session);
  if (!norm) return "";
  return `${normalizeStaffEmail(norm.email || "")}::${norm.role}`;
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
  const preserveRole = String(opts?.preserveRole || "").toLowerCase();
  const { localStore } = resolveMonitoringStores(opts);
  if (!localStore) return;
  let keepCacheKey = preserveEmail
    ? `${LS_AUTH_CACHE_PREFIX}${normalizeStaffEmail(preserveEmail)}`
    : "";
  if (keepCacheKey && preserveRole === "screener") {
    try {
      const parsed = JSON.parse(localStore.getItem(keepCacheKey) || "null");
      if (
        !parsed ||
        parsed.role !== "screener" ||
        "staffOnly" in parsed ||
        "pendingModerationCount" in parsed
      ) {
        keepCacheKey = "";
      }
    } catch (_e) {
      keepCacheKey = "";
    }
  }

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
  purgePrivateMonitoringStorage({
    localStore,
    preserveEmail: sess.email,
    preserveRole: sess.role,
  });
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
  notifySameTabSyncHooks(localStore, sessionStore);
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

  const prevLocalSession = readSessionFromStore(localStore);
  const prevSession = readActiveMonitoringSession({ localStore, sessionStore });
  const prevLocalIdentity = getSessionIdentityKey(prevLocalSession);
  const prevActiveIdentity = getSessionIdentityKey(prevSession);
  const capturedSessionEpoch = _authSessionEpoch;
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
      const liveLocalIdentity = getSessionIdentityKey(readSessionFromStore(localStore));
      const liveActiveIdentity = getSessionIdentityKey(
        readActiveMonitoringSession({ localStore, sessionStore })
      );
      if (
        _authSessionEpoch !== capturedSessionEpoch ||
        liveLocalIdentity !== prevLocalIdentity ||
        liveActiveIdentity !== prevActiveIdentity
      ) {
        return {
          ok: false,
          cancelled: true,
          error: "Authentication session changed while verification was in progress.",
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

  const resolvedIdentity = `${normalizeStaffEmail(resolvedEmail)}::${resolvedRole}`;
  const switchedAccountOrRole = Boolean(prevActiveIdentity && prevActiveIdentity !== resolvedIdentity);
  const localSwitchedAccountOrRole = Boolean(
    prevLocalIdentity && prevLocalIdentity !== resolvedIdentity
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
    if (localSwitchedAccountOrRole && localStore) {
      clearLegacyMirrorOnSignOut(localStore);
    }
    purgePrivateMonitoringStorage({
      preserveEmail: resolvedEmail,
      preserveRole: resolvedRole,
      localStore,
    });
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

  notifySameTabSyncHooks(localStore, sessionStore);
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
  _authSessionEpoch += 1;
  notifyCoordinatorClearHooks();
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
    const owner =
      (session ? normalizeMonitoringSession(session) : null) || readActiveMonitoringSession(opts);
    let causalSeq = _retryCausalSeq.get(retryFn);
    if (!causalSeq) {
      causalSeq = _nextCausalSeq++;
      _retryCausalSeq.set(retryFn, causalSeq);
    }
    insertRetryOrdered({
      retryFn,
      description,
      causalSeq,
      sessionEpoch: _authSessionEpoch,
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

function shouldEvictQueuedRetryOnAbort(
  cand,
  currentItem,
  capturedSessionEpoch,
  active,
  latestActive
) {
  if (cand.retryFn === currentItem?.retryFn) return true;
  const liveAuthorized = Boolean(latestActive && latestActive.authorized);
  if (liveAuthorized && !isRetryOwnedBySession(cand, latestActive)) return true;
  const ownedByInvalidatedActive = Boolean(active && isRetryOwnedBySession(cand, active));
  if (cand.sessionEpoch <= capturedSessionEpoch && (!liveAuthorized || ownedByInvalidatedActive)) {
    return true;
  }
  return Boolean(
    !liveAuthorized && ownedByInvalidatedActive && (cand.ownerEmail || cand.ownerRole)
  );
}

export async function replayPendingAuthRetries(opts = {}) {
  const { hasExplicitStore, localStore, sessionStore } = resolveMonitoringStores(opts);
  const hasExplicitSession = Boolean(
    opts && opts.session !== undefined && opts.session !== null
  );
  const explicitSession = hasExplicitSession ? normalizeMonitoringSession(opts.session) : null;
  const hasSessionContext = Boolean(
    hasExplicitSession || localStore || sessionStore || hasExplicitStore
  );
  const active = hasExplicitSession
    ? explicitSession
    : readActiveMonitoringSession({ localStore, sessionStore });
  if (hasSessionContext && (!active || !active.authorized)) {
    clearPendingAuthRetries();
    return [];
  }
  const capturedClearGen = _authRetryClearGen;
  const capturedSessionEpoch = _authSessionEpoch;
  const pending = _pendingAuthRetryQueue.splice(0, _pendingAuthRetryQueue.length);
  const results = [];
  const isReplayInvalidated = () => _authSessionEpoch !== capturedSessionEpoch;
  const abortInvalidatedReplay = (currentItem, nextIdx) => {
    const clearedAll = _authRetryClearGen !== capturedClearGen;
    const latestActive = readActiveMonitoringSession({ localStore, sessionStore });
    if (currentItem) {
      cancelQueuedRetryItem(currentItem);
    }
    for (let k = _pendingAuthRetryQueue.length - 1; k >= 0; k -= 1) {
      const cand = _pendingAuthRetryQueue[k];
      if (
        shouldEvictQueuedRetryOnAbort(
          cand,
          currentItem,
          capturedSessionEpoch,
          active,
          latestActive
        )
      ) {
        const [stale] = _pendingAuthRetryQueue.splice(k, 1);
        cancelQueuedRetryItem(stale);
      }
    }
    for (let j = nextIdx; j < pending.length; j += 1) {
      const rem = pending[j];
      const belongedToInvalidatedActive = Boolean(
        active && isRetryOwnedBySession(rem, active)
      );
      if (
        !clearedAll &&
        !belongedToInvalidatedActive &&
        latestActive &&
        latestActive.authorized &&
        isRetryOwnedBySession(rem, latestActive)
      ) {
        insertRetryOrdered(rem);
      } else {
        cancelQueuedRetryItem(rem);
      }
    }
  };
  for (let i = 0; i < pending.length; i += 1) {
    const item = pending[i];
    if (isReplayInvalidated()) {
      abortInvalidatedReplay(null, i);
      break;
    }
    if (active && !isRetryOwnedBySession(item, active)) {
      cancelQueuedRetryItem(item);
      continue;
    }
    try {
      const result = await item.retryFn();
      if (isReplayInvalidated()) {
        abortInvalidatedReplay(item, i + 1);
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
      if (isReplayInvalidated()) {
        abortInvalidatedReplay(item, i + 1);
        break;
      }
      results.push({ ok: false, error: err?.message || String(err), description: item.description });
    }
  }
  return results;
}

function captureInFlightSessionGuard(activeSession, explicitSession, opts, preResolvedLocalStore) {
  const capturedEpoch = _authSessionEpoch;
  const ownerIdentity = getSessionIdentityKey(activeSession);
  const resolvedStores = resolveMonitoringStores(opts);
  const localStore =
    preResolvedLocalStore !== undefined ? preResolvedLocalStore : resolvedStores.localStore;
  const storeOpts = { localStore, sessionStore: resolvedStores.sessionStore };
  const effectiveAtStart = explicitSession
    ? readActiveMonitoringSession(storeOpts)
    : activeSession;
  const sessionBackedByStore =
    !explicitSession || getSessionIdentityKey(effectiveAtStart) === ownerIdentity;
  // When localStore also matched at RPC start (normal mirrored sign-in rather than an intentional
  // tab-scoped sessionStore override), re-verify localStore directly after await so a sibling-tab
  // sign-out or role downgrade invalidates in-flight RPCs even before the browser's queued
  // `storage` event fires to evict the mirrored sessionStore copy.
  const backedByLocal =
    sessionBackedByStore &&
    getSessionIdentityKey(readSessionFromStore(localStore)) === ownerIdentity;
  return () => {
    if (_authSessionEpoch !== capturedEpoch) return false;
    if (sessionBackedByStore) {
      if (getSessionIdentityKey(readActiveMonitoringSession(storeOpts)) !== ownerIdentity) {
        return false;
      }
      if (
        backedByLocal &&
        getSessionIdentityKey(readSessionFromStore(localStore)) !== ownerIdentity
      ) {
        return false;
      }
      return true;
    }
    return Boolean(explicitSession && explicitSession.authorized);
  };
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
  const normalizedExplicit = session ? normalizeMonitoringSession(session) : null;
  const activeSession = session ? normalizedExplicit : readActiveMonitoringSession(opts);
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

  const isStillOwned = captureInFlightSessionGuard(activeSession, normalizedExplicit, opts);
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
      if (!isStillOwned()) {
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
  const { localStore } = resolveMonitoringStores(opts);
  const normalizedExplicit = session ? normalizeMonitoringSession(session) : null;
  const activeSession = session ? normalizedExplicit : readActiveMonitoringSession(opts);
  if (!activeSession || !activeSession.authorized) {
    return { ok: false, authorized: false, signedOut: true };
  }
  const isStillOwned = captureInFlightSessionGuard(
    activeSession,
    normalizedExplicit,
    opts,
    localStore
  );
  const resp = await callMonitoringRpc({
    ...opts,
    webhookUrl,
    action: "pull_monitoring_state",
    session: normalizedExplicit,
    fetchImpl,
    onTokenExpired,
  });
  if (!isStillOwned()) {
    return { ok: false, authorized: false, cancelled: true };
  }
  if (resp && resp.ok) {
    const rawRole = resp.role || activeSession.role || "staff";
    const effectiveRole = String(rawRole).toLowerCase() === "screener" ? "screener" : "staff";
    const localSess = readSessionFromStore(localStore);
    const normActiveEmail = normalizeStaffEmail(activeSession.email || "");
    if (
      getSessionIdentityKey(localSess) === `${normActiveEmail}::${effectiveRole}` ||
      (effectiveRole === "screener" &&
        localSess &&
        normalizeStaffEmail(localSess.email || "") === normActiveEmail)
    ) {
      saveCachedMonitoringState(activeSession.email, effectiveRole, resp, opts);
    }
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
  let lastLocalIdentity = getSessionIdentityKey(readSessionFromStore(localStore));
  let lastActiveIdentity = getSessionIdentityKey(
    readActiveMonitoringSession({ localStore, sessionStore })
  );
  // Browser `storage` events do not fire on the originating tab; refresh baselines after
  // same-tab sign-in/sign-out writes (including sessionStore-only overrides) so cross-tab
  // sign-outs recognize mirrored sessionStorage entries without clobbering tab overrides.
  const syncFromSameTabWrite = (writtenLocal, writtenSession) => {
    const isSameTab = sessionStore
      ? writtenSession === sessionStore
      : !writtenSession && Boolean(localStore && writtenLocal === localStore);
    if (isSameTab) {
      lastLocalIdentity = getSessionIdentityKey(readSessionFromStore(localStore));
      lastActiveIdentity = getSessionIdentityKey(
        readActiveMonitoringSession({ localStore, sessionStore })
      );
    }
  };
  _activeCrossTabSyncHooks.add(syncFromSameTabWrite);
  const handler = (ev) => {
    if (!ev) return;
    if (ev.key === LS_ADMIN_SESSION_KEY || ev.key === LS_CROSS_TAB_BROADCAST_KEY || ev.key === null) {
      const localSess = readSessionFromStore(localStore);
      const nextLocalIdentity = getSessionIdentityKey(localSess);
      if (localStore && sessionStore) {
        const tabSess = readSessionFromStore(sessionStore);
        const tabIdentity = getSessionIdentityKey(tabSess);
        const tabMirroredPriorLocal =
          Boolean(tabIdentity) &&
          Boolean(lastLocalIdentity) &&
          tabIdentity === lastLocalIdentity;
        if (tabMirroredPriorLocal && nextLocalIdentity !== tabIdentity) {
          try {
            sessionStore.removeItem(LS_ADMIN_SESSION_KEY);
          } catch (_e) {}
        }
      }
      const prevLocalIdentity = lastLocalIdentity;
      lastLocalIdentity = nextLocalIdentity;
      const currentSession = readActiveMonitoringSession({ localStore, sessionStore });
      const nextActiveIdentity = getSessionIdentityKey(currentSession);
      if (!currentSession) {
        clearPendingAuthRetries();
        purgePrivateMonitoringStorage({ localStore });
      } else {
        if (lastActiveIdentity !== nextActiveIdentity) {
          _authSessionEpoch += 1;
          notifyCoordinatorClearHooks();
        }
        if (prevLocalIdentity !== nextLocalIdentity || lastActiveIdentity !== nextActiveIdentity) {
          const sameEmailDowngraded = Boolean(
            localSess &&
              localSess.role === "screener" &&
              prevLocalIdentity === `${normalizeStaffEmail(localSess.email || "")}::staff`
          );
          purgePrivateMonitoringStorage({
            localStore,
            preserveEmail: localSess && !sameEmailDowngraded ? localSess.email : "",
            preserveRole: localSess ? localSess.role : "",
          });
        }
        for (let i = _pendingAuthRetryQueue.length - 1; i >= 0; i--) {
          if (!isRetryOwnedBySession(_pendingAuthRetryQueue[i], currentSession)) {
            const [removed] = _pendingAuthRetryQueue.splice(i, 1);
            cancelQueuedRetryItem(removed);
          }
        }
      }
      lastActiveIdentity = nextActiveIdentity;
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
    _activeCrossTabSyncHooks.delete(syncFromSameTabWrite);
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
  const committedTargetBySourcePid = new Map();
  const promotionSeqBySourcePid = new Map();
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

  function promotionPathReaches(startPid, goalPid) {
    let cur = String(startPid || "").trim();
    const goal = String(goalPid || "").trim();
    if (!cur || !goal) return false;
    const seen = new Set();
    while (cur && !seen.has(cur)) {
      if (cur === goal) return true;
      seen.add(cur);
      cur = promotedTargetBySourcePid.get(cur) || "";
    }
    return false;
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

  function hasAnyCoordinatorState() {
    return (
      activeBatchTickets.size > 0 ||
      recordsByParcelId.size > 0 ||
      promotedTargetBySourcePid.size > 0 ||
      committedTargetBySourcePid.size > 0
    );
  }

  function syncClearHookRegistration() {
    if (hasAnyCoordinatorState()) {
      _activeCoordinatorClearHooks.add(clearAll);
    } else {
      _activeCoordinatorClearHooks.delete(clearAll);
    }
  }

  function clearAll() {
    generation += 1;
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
    recordsByParcelId.clear();
    promotedTargetBySourcePid.clear();
    committedTargetBySourcePid.clear();
    promotionSeqBySourcePid.clear();
    _activeCoordinatorClearHooks.delete(clearAll);
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
    syncClearHookRegistration();
  }

  function captureBaselineExpectedTs(parcelId, fallbackDecidedAt = "") {
    const pid = String(parcelId || "").trim();
    if (!pid) return "";
    const rec = getOrCreateRecord(pid);
    if (!rec.hasServerTs) {
      rec.hasServerTs = true;
      rec.serverTs = String(fallbackDecidedAt || "").trim();
    }
    syncClearHookRegistration();
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
    promotionSeqBySourcePid.delete(toPid);
    promotedTargetBySourcePid.set(fromPid, toPid);
    promotionSeqBySourcePid.set(fromPid, _nextCausalSeq++);
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
        sessionEpoch: existingTo ? existingTo.sessionEpoch : priorFrom.sessionEpoch,
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
    const batchSessionEpoch = _authSessionEpoch;
    rec.pendingBatch = {
      baselineExpectedTs: prev ? prev.baselineExpectedTs : String(baselineExpectedTs || "").trim(),
      rootReplacePid,
      sessionEpoch: batchSessionEpoch,
      patchPayload: mergedPatch,
    };
    if (rec.timerId !== null) {
      cancelTimer(rec.timerId);
      rec.timerId = null;
    }
    const capturedGen = generation;
    rec.timerId = scheduleTimer(async () => {
      if (capturedGen !== generation || batchSessionEpoch !== _authSessionEpoch) {
        syncClearHookRegistration();
        return { ok: false, cancelled: true };
      }
      rec.timerId = null;
      const batch = rec.pendingBatch;
      if (!batch || batch.sessionEpoch !== _authSessionEpoch) {
        syncClearHookRegistration();
        return { ok: false, cancelled: true };
      }
      // Atomically transition pid from pendingBatch -> activeBatchTickets with zero microtask gap
      rec.pendingBatch = null;
      let retryCancelled = false;
      const isBatchCancelled = () =>
        capturedGen !== generation || retryCancelled || batch.sessionEpoch !== _authSessionEpoch;
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
        if (isBatchCancelled()) {
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
          if (isBatchCancelled()) {
            finishBatchTicket();
            return { ok: false, cancelled: true };
          }
        }
        const expectedTs = rec.hasServerTs ? rec.serverTs : batch.baselineExpectedTs;
        const attemptSeq = _nextCausalSeq++;
        let res;
        try {
          res = await sendDecisionRpc({
            parcelId: pid,
            expectedUpdatedAt: expectedTs,
            patch: batch.patchPayload,
            retryFn: sendRpc,
          });
          if (isBatchCancelled()) {
            return res;
          }
          if (res && res.queuedForRetry) {
            return res;
          }
          if (res && res.ok && res.decision && res.decision.parcel_id) {
            const srvPid = String(res.decision.parcel_id).trim();
            const srvTs = String(res.decision.decided_at || "").trim();
            const lineageRoot = resolveLatestTargetPid(pid) || pid;
            if (!promotedTargetBySourcePid.has(srvPid)) {
              committedTargetBySourcePid.delete(srvPid);
            }
            for (const [srcPid, targetPid] of promotedTargetBySourcePid.entries()) {
              if (
                (promotionSeqBySourcePid.get(srcPid) || 0) < attemptSeq &&
                (promotionPathReaches(targetPid, srvPid) ||
                  promotionPathReaches(targetPid, pid))
              ) {
                committedTargetBySourcePid.set(srcPid, targetPid);
              }
            }
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
          if (!(res && res.queuedForRetry && !isBatchCancelled())) {
            finishBatchTicket();
          }
        }
      };
      _retryCausalSeq.set(sendRpc, batchTicket.seq);
      sendRpc.cancelQueuedRetry = () => {
        if (capturedGen !== generation) {
          return;
        }
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
        const rollbackUpdates = [];
        for (const [srcPid, curTarget] of promotedTargetBySourcePid.entries()) {
          if ((resolveLatestTargetPid(srcPid) || srcPid) === rootTarget) {
            const committedTarget = committedTargetBySourcePid.get(srcPid) || "";
            if (committedTarget !== curTarget) {
              rollbackUpdates.push([srcPid, committedTarget]);
            }
          }
        }
        for (const [srcPid, committedTarget] of rollbackUpdates) {
          if (committedTarget && committedTarget !== srcPid) {
            promotedTargetBySourcePid.set(srcPid, committedTarget);
            promotionSeqBySourcePid.set(srcPid, 0);
          } else {
            promotedTargetBySourcePid.delete(srcPid);
            promotionSeqBySourcePid.delete(srcPid);
          }
        }
        // `promoteParcelKey(..., rootTarget)` deletes `rootTarget`'s outgoing edge to prevent cycles;
        // restore its last server-committed outgoing edge if doing so does not introduce a cycle.
        const restoredForRoot = committedTargetBySourcePid.get(rootTarget) || "";
        if (
          restoredForRoot &&
          restoredForRoot !== rootTarget &&
          !promotedTargetBySourcePid.has(rootTarget) &&
          !promotionPathReaches(restoredForRoot, rootTarget)
        ) {
          promotedTargetBySourcePid.set(rootTarget, restoredForRoot);
          promotionSeqBySourcePid.set(rootTarget, 0);
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
