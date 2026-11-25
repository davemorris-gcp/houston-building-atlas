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

// Fail closed to "screener" by default; session/verify parsers explicitly pass "staff" only for
// backward compatibility with pre-Phase-1 admin sessions and webhook payloads that omit `role`.
function normalizeRoleString(val, emptyFallback = "screener") {
  const cleaned = String(val ?? "").trim().toLowerCase();
  if (!cleaned) {
    return String(emptyFallback ?? "").trim().toLowerCase() === "staff" ? "staff" : "screener";
  }
  return cleaned === "staff" ? "staff" : "screener";
}

export function normalizeMonitoringSession(raw) {
  if (!raw || typeof raw !== "object" || !raw.authorized) return null;
  const email = normalizeStaffEmail(raw.email || "");
  const role = normalizeRoleString(raw.role, "staff");
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
  const { localStore, sessionStore } = resolveMonitoringStores(opts);
  if (!localStore) return;
  const normPreserveEmail = normalizeStaffEmail(preserveEmail);
  const activeSess =
    opts?.preserveRole === undefined && normPreserveEmail
      ? readActiveMonitoringSession({ localStore, sessionStore })
      : null;
  const inferredRole =
    opts?.preserveRole !== undefined
      ? opts.preserveRole
      : activeSess && normalizeStaffEmail(activeSess.email || "") === normPreserveEmail
      ? activeSess.role
      : "";
  const preserveRole = normalizeRoleString(inferredRole);
  let keepCacheKey = normPreserveEmail ? `${LS_AUTH_CACHE_PREFIX}${normPreserveEmail}` : "";
  // Fail-closed: only an explicitly preserved (or active same-email) "staff" session may retain `staffOnly` caches.
  if (keepCacheKey && preserveRole !== "staff") {
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
  const cleanRole = normalizeRoleString(role);
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
  const cleanExpectedRole = normalizeRoleString(expectedRole);
  try {
    const raw = localStore.getItem(`${LS_AUTH_CACHE_PREFIX}${normEmail}`);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return null;
    // Reject cached Staff state if user's role changed to Screener!
    if (cleanExpectedRole === "screener" && parsed.role !== "screener") {
      localStore.removeItem(`${LS_AUTH_CACHE_PREFIX}${normEmail}`);
      return null;
    }
    if (cleanExpectedRole === "screener") {
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
      resolvedRole = normalizeRoleString(verifyData.role, "staff");
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
    const resolvedOwner = { email: resolvedEmail, role: resolvedRole, authorized: true };
    evictQueuedRetriesWhere((cand) => !isRetryOwnedBySession(cand, resolvedOwner));
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

function evictQueuedRetriesWhere(shouldEvict) {
  for (let i = _pendingAuthRetryQueue.length - 1; i >= 0; i -= 1) {
    if (shouldEvict(_pendingAuthRetryQueue[i])) {
      const [removed] = _pendingAuthRetryQueue.splice(i, 1);
      cancelQueuedRetryItem(removed);
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

function resolveEffectiveSession(rawSession, storeOpts) {
  const hasExplicitSession = rawSession !== undefined && rawSession !== null;
  const explicitSession = hasExplicitSession ? normalizeMonitoringSession(rawSession) : null;
  return {
    hasExplicitSession,
    explicitSession,
    activeSession: hasExplicitSession ? explicitSession : readActiveMonitoringSession(storeOpts),
  };
}

/**
 * Queue a deferred RPC closure for replay after re-authentication. Callers holding external
 * concurrency tickets may attach `retryFn.cancelQueuedRetry = () => void` to release them on eviction.
 */
export function queuePendingAuthRetry(retryFn, description = "", session = null, opts = {}) {
  if (typeof retryFn === "function") {
    const { hasExplicitSession, activeSession: owner } = resolveEffectiveSession(session, opts);
    if (hasExplicitSession && !owner) {
      cancelQueuedRetryItem({ retryFn });
      return _pendingAuthRetryQueue.length;
    }
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
  // Evict retries queued before the invalidation boundary while sparing fresh retries queued
  // after a same-user re-sign-in (`cand.sessionEpoch > capturedSessionEpoch`).
  if (cand.sessionEpoch <= capturedSessionEpoch && (!liveAuthorized || ownedByInvalidatedActive)) {
    return true;
  }
  // Under explicit-session callers with empty stores (`!liveAuthorized`), an invalidated in-flight
  // closure resuming after `await` stamps child retries with the new epoch; evict those attributed
  // children without wiping unowned retries queued in the new epoch.
  return Boolean(
    !liveAuthorized && ownedByInvalidatedActive && (cand.ownerEmail || cand.ownerRole)
  );
}

export async function replayPendingAuthRetries(opts = {}) {
  const { hasExplicitStore, localStore, sessionStore } = resolveMonitoringStores(opts);
  const storeOpts = { localStore, sessionStore };
  const {
    hasExplicitSession,
    explicitSession,
    activeSession: active,
  } = resolveEffectiveSession(opts?.session, storeOpts);
  const hasSessionContext = Boolean(
    hasExplicitSession || localStore || sessionStore || hasExplicitStore
  );
  if (hasSessionContext && (!active || !active.authorized)) {
    clearPendingAuthRetries();
    return [];
  }
  const capturedClearGen = _authRetryClearGen;
  const capturedSessionEpoch = _authSessionEpoch;
  const isStillOwned = captureInFlightSessionGuard(active, explicitSession, storeOpts);
  const pending = _pendingAuthRetryQueue.splice(0, _pendingAuthRetryQueue.length);
  const results = [];
  const isReplayInvalidated = () => !isStillOwned();
  const abortInvalidatedReplay = (currentItem, nextIdx) => {
    const clearedAll = _authRetryClearGen !== capturedClearGen;
    const latestActive = readActiveMonitoringSession(storeOpts);
    if (currentItem) {
      cancelQueuedRetryItem(currentItem);
    }
    evictQueuedRetriesWhere((cand) =>
      shouldEvictQueuedRetryOnAbort(
        cand,
        currentItem,
        capturedSessionEpoch,
        active,
        latestActive
      )
    );
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

function captureInFlightSessionGuard(activeSession, explicitSession, opts = {}) {
  const capturedEpoch = _authSessionEpoch;
  const ownerIdentity = getSessionIdentityKey(activeSession);
  const { localStore, sessionStore } = resolveMonitoringStores(opts);
  const storeOpts = { localStore, sessionStore };
  const effectiveAtStart = explicitSession
    ? readActiveMonitoringSession(storeOpts)
    : activeSession;
  const sessionBackedByStore = Boolean(
    activeSession && (!explicitSession || getSessionIdentityKey(effectiveAtStart) === ownerIdentity)
  );
  // When localStore also matched at RPC start (normal mirrored sign-in rather than an intentional
  // tab-scoped sessionStore override), re-verify localStore directly after await so a sibling-tab
  // sign-out or role downgrade invalidates in-flight RPCs even before the browser's queued
  // `storage` event fires to evict the mirrored sessionStore copy.
  const backedByLocal = Boolean(
    sessionBackedByStore &&
      getSessionIdentityKey(readSessionFromStore(localStore)) === ownerIdentity
  );
  const reconcileDivergence = () => {
    if (!backedByLocal) return false;
    // Microtask continuations resume before the browser's queued cross-tab `storage` macrotask;
    // evict the stale mirrored sessionStore copy and advance `_authSessionEpoch` now, but do NOT
    // call `notifySameTabSyncHooks` so `subscribeCrossTabAuthChanges` still observes the
    // `lastActiveIdentity !== nextActiveIdentity` transition when the `storage` event fires.
    const diverged =
      getSessionIdentityKey(readSessionFromStore(localStore)) !== ownerIdentity;
    if (
      diverged &&
      sessionStore &&
      getSessionIdentityKey(readSessionFromStore(sessionStore)) === ownerIdentity
    ) {
      try {
        sessionStore.removeItem(LS_ADMIN_SESSION_KEY);
      } catch (_e) {}
    }
    if (diverged && _authSessionEpoch === capturedEpoch) {
      _authSessionEpoch += 1;
      notifyCoordinatorClearHooks();
    }
    return diverged;
  };
  return () => {
    if (_authSessionEpoch !== capturedEpoch) {
      reconcileDivergence();
      return false;
    }
    if (!activeSession) return true;
    if (sessionBackedByStore) {
      if (reconcileDivergence()) return false;
      return getSessionIdentityKey(readActiveMonitoringSession(storeOpts)) === ownerIdentity;
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
  const { localStore, sessionStore } = resolveMonitoringStores(opts);
  const storeOpts = { localStore, sessionStore };
  const { explicitSession, activeSession } = resolveEffectiveSession(session, storeOpts);
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

  const isStillOwned = captureInFlightSessionGuard(activeSession, explicitSession, storeOpts);
  try {
    const res = await doFetch(targetUrl, {
      method: "POST",
      headers: { "Content-Type": "text/plain;charset=utf-8" },
      body: JSON.stringify(reqPayload),
    });
    if (!res || !res.ok) {
      if (!isStillOwned()) {
        return { ok: false, authorized: false, cancelled: true };
      }
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
    if (!isStillOwned()) {
      return { ok: false, authorized: false, cancelled: true };
    }
    return data;
  } catch (err) {
    if (!isStillOwned()) {
      return { ok: false, authorized: false, cancelled: true };
    }
    return { ok: false, error: err?.message || String(err) };
  }
}

function reconcileScreenerPullDowngrade({
  activeSession,
  normActiveEmail,
  localStore,
  sessionStore,
}) {
  purgePrivateMonitoringStorage({
    localStore,
    preserveEmail: activeSession.email,
    preserveRole: "screener",
  });
  let downgraded = false;
  for (const store of [sessionStore, localStore]) {
    const sess = readSessionFromStore(store);
    if (
      !store ||
      !sess ||
      normalizeStaffEmail(sess.email || "") !== normActiveEmail ||
      sess.role === "screener"
    ) {
      continue;
    }
    downgraded = true;
    try {
      store.setItem(
        LS_ADMIN_SESSION_KEY,
        JSON.stringify({ ...sess, role: "screener", displayLabel: "Screener" })
      );
    } catch (_e) {
      try {
        store.removeItem(LS_ADMIN_SESSION_KEY);
      } catch (_e2) {}
    }
  }
  if (downgraded || activeSession.role !== "screener") {
    _authSessionEpoch += 1;
    notifyCoordinatorClearHooks();
  }
  if (downgraded) {
    notifySameTabSyncHooks(localStore, sessionStore);
  }
  const updatedSession = downgraded
    ? readActiveMonitoringSession({ localStore, sessionStore })
    : { email: normActiveEmail, role: "screener", authorized: true };
  evictQueuedRetriesWhere((cand) =>
    downgraded
      ? !updatedSession || !isRetryOwnedBySession(cand, updatedSession)
      : cand.ownerEmail === normActiveEmail && !isRetryOwnedBySession(cand, updatedSession)
  );
}

export async function pullCloudMonitoringState(opts = {}) {
  const {
    webhookUrl = DEFAULT_SHEET_WEBHOOK_URL,
    session = null,
    fetchImpl = null,
    onTokenExpired = null,
  } = opts || {};
  const { localStore, sessionStore } = resolveMonitoringStores(opts);
  const storeOpts = { localStore, sessionStore };
  const { explicitSession, activeSession } = resolveEffectiveSession(session, storeOpts);
  if (!activeSession || !activeSession.authorized) {
    return { ok: false, authorized: false, signedOut: true };
  }
  const isStillOwned = captureInFlightSessionGuard(activeSession, explicitSession, storeOpts);
  const resp = await callMonitoringRpc({
    ...opts,
    webhookUrl,
    action: "pull_monitoring_state",
    session: explicitSession,
    fetchImpl,
    onTokenExpired,
  });
  if (!isStillOwned()) {
    return { ok: false, authorized: false, cancelled: true };
  }
  if (resp && resp.ok) {
    const effectiveRole = normalizeRoleString(resp.role, activeSession.role);
    resp.role = effectiveRole;
    const normActiveEmail = normalizeStaffEmail(activeSession.email || "");
    if (effectiveRole === "screener") {
      delete resp.staffOnly;
      delete resp.pendingModerationCount;
      reconcileScreenerPullDowngrade({
        activeSession,
        normActiveEmail,
        localStore,
        sessionStore,
      });
    }
    const refreshedLocalSess = readSessionFromStore(localStore);
    const sameLocalEmail =
      Boolean(refreshedLocalSess) &&
      normalizeStaffEmail(refreshedLocalSess.email || "") === normActiveEmail;
    // Allow same-email Screener pulls to overwrite stale localStore caches on server-side
    // role downgrade, while preventing tab-scoped Staff pulls from writing into Screener localStore.
    if (
      sameLocalEmail &&
      (effectiveRole === "screener" || refreshedLocalSess.role === effectiveRole)
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
          purgePrivateMonitoringStorage({
            localStore,
            preserveEmail: localSess ? localSess.email : "",
            preserveRole: localSess ? localSess.role : "",
          });
        }
        evictQueuedRetriesWhere((cand) => !isRetryOwnedBySession(cand, currentSession));
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
 *
 * Promotion contract: callers renaming `fromPid -> toPid` (e.g. `CAMPUS:<SLUG>`) invoke
 * `promoteParcelKey(fromPid, toPid, fallbackDecidedAt)` before `enqueue(toPid, patch, ts)`;
 * calling `enqueue(pid, ...)` directly treats `pid` as standalone, detaching outgoing promotions
 * optimistically at enqueue time, committing the standalone state on success, and restoring any
 * prior server-committed promotion if the batch fails or is evicted.
 */
export function createParcelSyncCoordinator({
  debounceMs = 650,
  sendDecisionRpc,
  onSuccess = null,
  onConflict = null,
  scheduleTimer = (fn, ms) => setTimeout(fn, ms),
  cancelTimer = (id) => clearTimeout(id),
} = {}) {
  // Sentinel causalSeq (< _nextCausalSeq starting at 1) marking already server-committed edges.
  const COMMITTED_EDGE_SEQ = 0;
  const UNCLAIMED_EDGE = Symbol("unclaimedEdge");

  const recordsByParcelId = new Map();
  // Maps `srcPid -> { targetPid, seq, ownerTokens }` where `ownerTokens` is a `Set` containing
  // `UNCLAIMED_EDGE` when staged by `promoteParcelKey`, one or more batch `Symbol`s while claimed
  // by uncommitted batches (including bypassed multi-hop edges), or empty once server-committed.
  const promotedEdgeBySourcePid = new Map();
  const committedLineageByPid = new Map();
  const activeBatchTickets = new Set();
  let generation = 0;

  function setPromotedEdge(
    srcPid,
    targetPid,
    seq,
    ownerTokens = new Set([UNCLAIMED_EDGE])
  ) {
    promotedEdgeBySourcePid.set(srcPid, {
      targetPid,
      seq,
      ownerTokens: new Set(ownerTokens),
    });
  }

  function deletePromotedEdge(srcPid) {
    promotedEdgeBySourcePid.delete(srcPid);
  }

  function hasIncomingPromotedEdge(targetPid) {
    for (const edge of promotedEdgeBySourcePid.values()) {
      if (edge.targetPid === targetPid) return true;
    }
    return false;
  }

  function hasUncommittedIncomingPromotedEdge(targetPid) {
    if (!targetPid) return false;
    for (const [srcPid, edge] of promotedEdgeBySourcePid.entries()) {
      if (edge.targetPid === targetPid && !isEdgeCommitted(srcPid, targetPid)) {
        return true;
      }
    }
    return false;
  }

  function lineagePathReaches(startPid, goalPid, getNextPid) {
    let cur = String(startPid || "").trim();
    const goal = String(goalPid || "").trim();
    if (!cur || !goal) return false;
    const seen = new Set();
    while (cur && !seen.has(cur)) {
      if (cur === goal) return true;
      seen.add(cur);
      cur = getNextPid(cur) || "";
    }
    return false;
  }

  function committedPathReaches(startPid, goalPid) {
    return lineagePathReaches(
      startPid,
      goalPid,
      (cur) => committedLineageByPid.get(cur)?.targetPid
    );
  }

  function isEdgeCommitted(srcPid, targetPid) {
    return Boolean(targetPid && srcPid !== targetPid && committedPathReaches(srcPid, targetPid));
  }

  function promotionPathReaches(
    startPid,
    goalPid,
    maxCausalSeq = Infinity,
    canTraverseEdge = null
  ) {
    return lineagePathReaches(startPid, goalPid, (cur) => {
      const nextEdge = promotedEdgeBySourcePid.get(cur);
      if (!nextEdge || nextEdge.seq >= maxCausalSeq) return "";
      if (canTraverseEdge && !canTraverseEdge(cur, nextEdge)) return "";
      return nextEdge.targetPid;
    });
  }

  function detachOutgoingPromotedEdge(nodePid, incomingSourcePid = "") {
    const prevOut = promotedEdgeBySourcePid.get(nodePid);
    if (!prevOut) return false;
    deletePromotedEdge(nodePid);
    const bypassTarget = prevOut.targetPid;
    const prevCommitted = isEdgeCommitted(nodePid, bypassTarget);
    const canBypassAcrossPrev = prevCommitted || !prevOut.ownerTokens.has(UNCLAIMED_EDGE);
    if (
      bypassTarget &&
      bypassTarget !== nodePid &&
      canBypassAcrossPrev &&
      (!incomingSourcePid ||
        (bypassTarget !== incomingSourcePid &&
          !promotionPathReaches(bypassTarget, incomingSourcePid)))
    ) {
      for (const [srcPid, edge] of promotedEdgeBySourcePid.entries()) {
        if (
          edge.targetPid === nodePid &&
          srcPid !== bypassTarget &&
          !promotionPathReaches(bypassTarget, srcPid)
        ) {
          const nextOwners = new Set();
          if (!isEdgeCommitted(srcPid, bypassTarget)) {
            if (!isEdgeCommitted(srcPid, nodePid)) {
              for (const tok of edge.ownerTokens) nextOwners.add(tok);
            }
            if (!prevCommitted) {
              for (const tok of prevOut.ownerTokens) nextOwners.add(tok);
            }
          }
          setPromotedEdge(
            srcPid,
            bypassTarget,
            Math.max(edge.seq, prevOut.seq),
            nextOwners
          );
        }
      }
    }
    return true;
  }

  function resolveLatestTargetPid(startPid) {
    let cur = String(startPid || "").trim();
    const seen = new Set();
    while (cur && promotedEdgeBySourcePid.has(cur) && !seen.has(cur)) {
      seen.add(cur);
      cur = promotedEdgeBySourcePid.get(cur).targetPid;
    }
    return cur;
  }

  function buildTouchedPids(targetPid, lineageEdges, rootReplacePid = "", patchReplacePid = "") {
    const touched = new Set();
    if (targetPid) touched.add(targetPid);
    if (rootReplacePid) touched.add(rootReplacePid);
    const normPatchReplace = String(patchReplacePid || "").trim();
    if (normPatchReplace) touched.add(normPatchReplace);
    for (const { srcPid, targetPid: dstPid } of lineageEdges) {
      if (srcPid) touched.add(srcPid);
      if (dstPid) touched.add(dstPid);
    }
    return touched;
  }

  function isUncommittedEdgeClaimableBy(edge, batchToken = null) {
    if (!edge || edge.ownerTokens.size === 0) return false;
    for (const tok of edge.ownerTokens) {
      if (tok !== UNCLAIMED_EDGE && (!batchToken || tok !== batchToken)) {
        return false;
      }
    }
    return true;
  }

  // Snapshot and claim pre-batch hops at enqueue/promote time so concurrent in-flight
  // re-promotions cannot erase committed hops, bridge unsent hops, or leak foreign/failed
  // uncommitted hops across detached intermediate parcels.
  function claimAndSnapshotLineageEdges(targetPid, maxCausalSeq, batchToken) {
    const canUseEdge = (srcPid, edge) =>
      isEdgeCommitted(srcPid, edge.targetPid) || isUncommittedEdgeClaimableBy(edge, batchToken);
    const snapshot = [];
    for (const [srcPid, edge] of promotedEdgeBySourcePid.entries()) {
      if (
        edge.seq < maxCausalSeq &&
        canUseEdge(srcPid, edge) &&
        promotionPathReaches(edge.targetPid, targetPid, maxCausalSeq, canUseEdge)
      ) {
        edge.ownerTokens.clear();
        if (!isEdgeCommitted(srcPid, edge.targetPid) && batchToken) {
          edge.ownerTokens.add(batchToken);
        }
        snapshot.push({ srcPid, targetPid: edge.targetPid, seq: edge.seq });
      }
    }
    return snapshot;
  }

  function getBatchReplaceParcelId(batch) {
    if (!batch) return "";
    return batch.rootReplacePid || String(batch.patchPayload?.replace_parcel_id || "").trim();
  }

  function hasNewerRedirectedPath(startPid, goalPid, minEdgeSeq = 0) {
    if (promotionPathReaches(startPid, goalPid)) return false;
    let cur = String(startPid || "").trim();
    const seen = new Set();
    while (cur && !seen.has(cur)) {
      seen.add(cur);
      const liveEdge = promotedEdgeBySourcePid.get(cur);
      if (!liveEdge) return false;
      if ((liveEdge.seq || 0) > minEdgeSeq) return true;
      cur = liveEdge.targetPid || "";
    }
    return false;
  }

  function isReplacePidSuperseded(batch, pid, replacePid) {
    if (!batch || !replacePid || replacePid === pid) return false;
    const committed = committedLineageByPid.get(replacePid);
    if (!committed) return false;
    const edge = Array.isArray(batch.lineageEdges)
      ? batch.lineageEdges.find((e) => e.srcPid === replacePid)
      : null;
    const replaceSeq = edge?.seq || batch.replaceCausalSeq || 0;
    if (!replaceSeq || (committed.seq || 0) <= replaceSeq) return false;
    return committed.targetPid === "" || !committedPathReaches(replacePid, pid);
  }

  function captureBatchLineageStamp(
    targetPid,
    batchToken,
    {
      priorLineageEdges = null,
      rootReplacePid = "",
      patchReplacePid = "",
    } = {}
  ) {
    const causalSeq = _nextCausalSeq++;
    const bySrc = new Map();
    // Retain previously snapshotted hops across coalesced edits or re-stamps even if a concurrent
    // standalone `enqueue(src)` detached `src -> targetPid` from `promotedEdgeBySourcePid` (unless
    // `src` was explicitly retargeted onto a newer promotion path away from `targetPid`); collapse
    // intermediate targets to `targetPid` if their downstream path to `targetPid` was broken.
    if (Array.isArray(priorLineageEdges)) {
      for (const edge of priorLineageEdges) {
        if (!edge?.srcPid || !edge?.targetPid || edge.srcPid === targetPid) continue;
        if (hasNewerRedirectedPath(edge.srcPid, targetPid, edge.seq || 0)) {
          continue;
        }
        const hopTarget =
          edge.targetPid === targetPid || promotionPathReaches(edge.targetPid, targetPid)
            ? edge.targetPid
            : targetPid;
        bySrc.set(edge.srcPid, {
          srcPid: edge.srcPid,
          targetPid: hopTarget,
          seq: edge.seq,
        });
      }
    }
    for (const edge of claimAndSnapshotLineageEdges(targetPid, causalSeq, batchToken)) {
      bySrc.set(edge.srcPid, edge);
    }
    const lineageEdges = Array.from(bySrc.values());
    return {
      batchToken,
      causalSeq,
      lineageEdges,
      touchedPids: buildTouchedPids(targetPid, lineageEdges, rootReplacePid, patchReplacePid),
    };
  }

  function syncPatchReplaceParcelId(patchObj, rootReplacePid) {
    if (!patchObj) return;
    if (rootReplacePid) {
      patchObj.replace_parcel_id = rootReplacePid;
    } else {
      delete patchObj.replace_parcel_id;
    }
  }

  function clampVacatedBaselineTs(targetPid, rootReplacePid, rawTs) {
    const cleanTs = String(rawTs || "").trim();
    if (!cleanTs) return "";
    const guardPid = rootReplacePid || targetPid;
    const guardFloor = guardPid ? recordsByParcelId.get(guardPid)?.vacatedFloorTs || "" : "";
    return guardFloor && cleanTs <= guardFloor ? "" : cleanTs;
  }

  function hasServerBackedRow(parcelId) {
    const pid = String(parcelId || "").trim();
    if (!pid) return false;
    if ((recordsByParcelId.get(pid)?.confirmedServerTs ?? null) !== null) {
      return true;
    }
    // Commits with blank `decided_at` (or blank-timestamp 409 conflicts) leave `confirmedServerTs`
    // null; fall back to `committedLineageByPid` unless `pid` itself committed an outgoing hop.
    const ownCommitted = committedLineageByPid.get(pid);
    if (ownCommitted?.targetPid) {
      return false;
    }
    if (ownCommitted?.hasServerRow) {
      return true;
    }
    for (const entry of committedLineageByPid.values()) {
      if (entry.targetPid === pid) return true;
    }
    return false;
  }

  function resolveUncommittedUpstreamOrigin(
    startPid,
    stopPid = "",
    allowedToken = null,
    { skipStartServerRow = false, blockedPids = null } = {}
  ) {
    let cur = String(startPid || "").trim();
    const initialPid = cur;
    const seen = new Set(stopPid ? [stopPid] : []);
    if (blockedPids) {
      // Exclude `initialPid` so iteration 0 still runs and can walk non-blocked incoming branches.
      for (const bPid of blockedPids) {
        if (bPid && bPid !== initialPid) seen.add(bPid);
      }
    }
    while (cur && !seen.has(cur)) {
      seen.add(cur);
      if ((cur !== initialPid || !skipStartServerRow) && hasServerBackedRow(cur)) break;
      let incomingSrc = "";
      for (const [srcPid, edge] of promotedEdgeBySourcePid.entries()) {
        if (
          !seen.has(srcPid) &&
          edge.targetPid === cur &&
          !isEdgeCommitted(srcPid, cur) &&
          isUncommittedEdgeClaimableBy(edge, allowedToken)
        ) {
          if (incomingSrc) {
            incomingSrc = "";
            break;
          }
          incomingSrc = srcPid;
        }
      }
      if (!incomingSrc) break;
      cur = incomingSrc;
    }
    return cur;
  }

  function resolveBatchReplaceLineage(
    ownerPid,
    targetPid,
    prevBatch,
    fallbackReplacePid = "",
    fallbackSeq = 0,
    blockedFallbackPids = null
  ) {
    const prevRepPid = getBatchReplaceParcelId(prevBatch);
    const validPrevRepPid =
      prevRepPid &&
      !isReplacePidSuperseded(prevBatch, ownerPid, prevRepPid) &&
      !hasNewerRedirectedPath(prevRepPid, targetPid, prevBatch?.replaceCausalSeq || 0)
        ? prevRepPid
        : "";
    const prevRepSuperseded = Boolean(prevBatch && prevRepPid && !validPrevRepPid);
    const cleanFallback = String(fallbackReplacePid || "").trim();
    const resolvedFallback = resolveUncommittedUpstreamOrigin(
      cleanFallback,
      targetPid,
      prevBatch?.batchToken,
      { blockedPids: blockedFallbackPids }
    );
    const fallbackHasServerRow = Boolean(
      resolvedFallback && resolvedFallback !== targetPid && hasServerBackedRow(resolvedFallback)
    );
    const recordedBouncePid =
      prevBatch?.bouncedFromPid ||
      (ownerPid === targetPid ? recordsByParcelId.get(targetPid)?.bouncedFromPid || "" : "");
    // Renaming B -> A after an unsent A -> B promotion invokes `promoteParcelKey(B, A)` followed
    // immediately by `enqueue(A, { replace_parcel_id: B })`: drop cancelled bounce `B` during both
    // the bounce promote (`validPrevRepPid === targetPid`) and follow-up enqueue (`bouncedFromPid`),
    // falling back to `targetPid`'s own uncommitted upstream origin (e.g. `X -> A` in `X -> A -> B`).
    const effectiveFallback =
      recordedBouncePid && cleanFallback === recordedBouncePid
        ? resolveUncommittedUpstreamOrigin(targetPid, "", prevBatch?.batchToken, {
            skipStartServerRow: true,
          })
        : resolvedFallback;
    const abandonedPrevRep = Boolean(
      validPrevRepPid &&
        validPrevRepPid !== targetPid &&
        blockedFallbackPids?.has(validPrevRepPid)
    );
    const targetOwnOrigin = abandonedPrevRep
      ? resolveUncommittedUpstreamOrigin(targetPid, ownerPid, prevBatch?.batchToken, {
          skipStartServerRow: true,
          blockedPids: blockedFallbackPids,
        })
      : "";
    let rootReplacePid = "";
    if (abandonedPrevRep && targetOwnOrigin && targetOwnOrigin !== targetPid) {
      rootReplacePid = targetOwnOrigin;
    } else if ((validPrevRepPid === targetPid || abandonedPrevRep) && !fallbackHasServerRow) {
      rootReplacePid = "";
    } else if (validPrevRepPid && validPrevRepPid !== targetPid && !abandonedPrevRep) {
      rootReplacePid = validPrevRepPid;
    } else if (effectiveFallback && effectiveFallback !== targetPid) {
      rootReplacePid = effectiveFallback;
    }
    const bouncedFromPid =
      ownerPid !== targetPid && !rootReplacePid
        ? ownerPid
        : !rootReplacePid
        ? recordedBouncePid
        : "";
    if (!rootReplacePid) {
      return {
        bouncedFromPid,
        rootReplacePid: "",
        replaceCausalSeq: 0,
        prevRepSuperseded,
      };
    }
    const inheritedSeq =
      prevBatch && validPrevRepPid === rootReplacePid ? prevBatch.replaceCausalSeq || 0 : 0;
    const liveEdgeSeq = promotionPathReaches(rootReplacePid, targetPid)
      ? promotedEdgeBySourcePid.get(rootReplacePid)?.seq || 0
      : 0;
    return {
      bouncedFromPid: "",
      rootReplacePid,
      replaceCausalSeq: Math.max(inheritedSeq, liveEdgeSeq) || fallbackSeq,
      prevRepSuperseded,
    };
  }

  function restampPendingBatch(targetPid, pendingBatch) {
    if (!pendingBatch) return;
    if (pendingBatch.rootReplacePid && promotionPathReaches(pendingBatch.rootReplacePid, targetPid)) {
      const liveSeq = promotedEdgeBySourcePid.get(pendingBatch.rootReplacePid)?.seq || 0;
      if (liveSeq > (pendingBatch.replaceCausalSeq || 0)) {
        pendingBatch.replaceCausalSeq = liveSeq;
      }
    }
    Object.assign(
      pendingBatch,
      captureBatchLineageStamp(targetPid, pendingBatch.batchToken, {
        priorLineageEdges: pendingBatch.lineageEdges,
        rootReplacePid: pendingBatch.rootReplacePid,
        patchReplacePid: pendingBatch.patchPayload?.replace_parcel_id,
      })
    );
  }

  function getPromotedTarget(parcelId) {
    return resolveLatestTargetPid(parcelId);
  }

  function getOrCreateRecord(pid) {
    let rec = recordsByParcelId.get(pid);
    if (!rec) {
      // `serverTs`: null = unseeded, "" = seeded uncreated baseline, ISO = provisional/confirmed ts.
      // `confirmedServerTs`: null = unconfirmed/vacated, ISO = backed by a live `DEC:<pid>` row.
      // `vacatedFloorTs`: monotonic cloud-pull guard preventing stale pre-vacation resurrection.
      // `bouncedFromPid`: cancelled bounce source pid awaiting phase-2 `enqueue` when `pendingBatch` is null.
      rec = {
        serverTs: null,
        confirmedServerTs: null,
        vacatedFloorTs: "",
        bouncedFromPid: "",
        pendingBatch: null,
        timerId: null,
      };
      recordsByParcelId.set(pid, rec);
    }
    return rec;
  }

  function setsIntersect(setA, setB) {
    if (!setA || !setB) return false;
    for (const item of setA) {
      if (setB.has(item)) return true;
    }
    return false;
  }

  function batchOverlapsTarget(ownerPid, touchedPids, pid, targetPid, extraPids = null) {
    if ((resolveLatestTargetPid(ownerPid) || ownerPid) === targetPid) {
      return true;
    }
    if (!touchedPids) return false;
    return (
      touchedPids.has(pid) ||
      touchedPids.has(targetPid) ||
      setsIntersect(extraPids, touchedPids)
    );
  }

  function hasPendingOrInFlight(parcelId) {
    const pid = String(parcelId || "").trim();
    if (!pid) return false;
    const targetPid = resolveLatestTargetPid(pid) || pid;
    for (const t of activeBatchTickets) {
      if (batchOverlapsTarget(t.pid, t.touchedPids, pid, targetPid)) {
        return true;
      }
    }
    for (const [recPid, candRec] of recordsByParcelId.entries()) {
      if (
        candRec.pendingBatch &&
        batchOverlapsTarget(recPid, candRec.pendingBatch.touchedPids, pid, targetPid)
      ) {
        return true;
      }
    }
    return false;
  }

  function getBlockingTicketPromise(pid, maxSeq = Infinity, candidatePids = null) {
    const targetPid = resolveLatestTargetPid(pid) || pid;
    for (const t of activeBatchTickets) {
      if (
        t.seq < maxSeq &&
        batchOverlapsTarget(t.pid, t.touchedPids, pid, targetPid, candidatePids)
      ) {
        return t.promise;
      }
    }
    return null;
  }

  function hasNewerOverlappingBatch(pid, minBatchSeq, minTicketSeq) {
    const targetPid = resolveLatestTargetPid(pid) || pid;
    for (const t of activeBatchTickets) {
      if (t.seq > minTicketSeq && batchOverlapsTarget(t.pid, t.touchedPids, pid, targetPid)) {
        return true;
      }
    }
    for (const [recPid, candRec] of recordsByParcelId.entries()) {
      if (
        candRec.pendingBatch &&
        candRec.pendingBatch.causalSeq > minBatchSeq &&
        batchOverlapsTarget(recPid, candRec.pendingBatch.touchedPids, pid, targetPid)
      ) {
        return true;
      }
    }
    return false;
  }

  function commitLineageEdgesBeforeSeq(
    pid,
    srvPid,
    maxCausalSeq,
    batchLineageEdges = [],
    batchToken = null
  ) {
    const eligibleBySrc = new Map();
    for (const { srcPid, targetPid, seq: edgeSeq } of batchLineageEdges) {
      eligibleBySrc.set(srcPid, { targetPid, seq: edgeSeq || maxCausalSeq });
    }
    if (srvPid && srvPid !== pid) {
      for (const { srcPid, targetPid, seq: edgeSeq } of claimAndSnapshotLineageEdges(
        srvPid,
        maxCausalSeq,
        batchToken
      )) {
        eligibleBySrc.set(srcPid, { targetPid, seq: edgeSeq || maxCausalSeq });
      }
    }
    eligibleBySrc.delete(srvPid);
    const canAdvanceCommitted = (nodePid, commitSeq = maxCausalSeq) =>
      (committedLineageByPid.get(nodePid)?.seq || 0) < commitSeq;
    const nextTargetByPid = new Map();
    for (const [srcPid, { targetPid, seq: commitSeq }] of eligibleBySrc.entries()) {
      if (
        srcPid &&
        targetPid &&
        srcPid !== targetPid &&
        canAdvanceCommitted(srcPid, commitSeq)
      ) {
        nextTargetByPid.set(srcPid, { targetPid, seq: commitSeq });
      }
    }
    // Retain `{ targetPid: "", seq: maxCausalSeq }` tombstones on terminal roots rather than
    // deleting entries so lower-seq out-of-order commits cannot resurrect cleared outgoing edges.
    for (const rootPid of new Set([srvPid, pid])) {
      if (!rootPid || eligibleBySrc.has(rootPid)) continue;
      const outEdge = promotedEdgeBySourcePid.get(rootPid);
      if ((!outEdge || outEdge.seq >= maxCausalSeq) && canAdvanceCommitted(rootPid)) {
        nextTargetByPid.set(rootPid, {
          targetPid: "",
          seq: maxCausalSeq,
          hasServerRow: rootPid === srvPid,
        });
      }
    }
    // When an intermediate parcel clears its committed outgoing edge (`rootPid -> prevCommittedTarget`),
    // bypass upstream committed pointers (`upPid -> rootPid`) onto `prevCommittedTarget` so multi-hop
    // chains (`A -> B -> C`) continue resolving `A -> C` after `B` saves standalone.
    for (const [rootPid, { targetPid: nextTarget }] of Array.from(nextTargetByPid.entries())) {
      if (nextTarget !== "") continue;
      const prevRootCommitted = committedLineageByPid.get(rootPid);
      const prevCommittedTarget = prevRootCommitted?.targetPid || "";
      if (!prevCommittedTarget || eligibleBySrc.has(prevCommittedTarget)) continue;
      for (const [upPid, { targetPid: upTarget, seq: upSeq }] of committedLineageByPid.entries()) {
        const bypassSeq = Math.max(upSeq || 0, prevRootCommitted?.seq || 0) || maxCausalSeq;
        if (
          upTarget === rootPid &&
          !nextTargetByPid.has(upPid) &&
          upPid !== prevCommittedTarget &&
          canAdvanceCommitted(upPid, bypassSeq)
        ) {
          nextTargetByPid.set(upPid, {
            targetPid: prevCommittedTarget,
            seq: bypassSeq,
            hasServerRow: false,
          });
        }
      }
    }
    // Clear stale outgoing targets for all updated nodes before checking cycle-freedom:
    // `Map` iterates in insertion order rather than topological order, so updating in a
    // single pass would reject reversed hops (e.g. B->A->C after prior A->B) as false cycles
    // against pre-batch committed edges that this commit is overwriting.
    for (const [nodePid, { targetPid, seq: commitSeq, hasServerRow }] of nextTargetByPid.entries()) {
      committedLineageByPid.set(nodePid, {
        targetPid: "",
        seq: commitSeq,
        hasServerRow: Boolean(!targetPid && hasServerRow),
      });
    }
    for (const [srcPid, { targetPid, seq: commitSeq }] of nextTargetByPid.entries()) {
      if (targetPid && !committedPathReaches(targetPid, srcPid)) {
        committedLineageByPid.set(srcPid, { targetPid, seq: commitSeq, hasServerRow: false });
      }
    }
    for (const [srcPid, liveEdge] of promotedEdgeBySourcePid.entries()) {
      if (batchToken) {
        liveEdge.ownerTokens.delete(batchToken);
      }
      if (isEdgeCommitted(srcPid, liveEdge.targetPid)) {
        liveEdge.ownerTokens.clear();
      } else if (liveEdge.ownerTokens.size === 0) {
        deletePromotedEdge(srcPid);
      }
    }
  }

  function evictBatchUncommittedEdges(batch) {
    if (!batch) return;
    if (Array.isArray(batch.lineageEdges)) {
      for (const { srcPid, targetPid, seq: edgeSeq } of batch.lineageEdges) {
        const liveEdge = promotedEdgeBySourcePid.get(srcPid);
        if (
          liveEdge &&
          liveEdge.seq === edgeSeq &&
          liveEdge.targetPid === targetPid &&
          !isEdgeCommitted(srcPid, liveEdge.targetPid)
        ) {
          deletePromotedEdge(srcPid);
        }
      }
    }
    if (batch.batchToken) {
      for (const [srcPid, liveEdge] of promotedEdgeBySourcePid.entries()) {
        if (
          liveEdge.ownerTokens.has(batch.batchToken) &&
          !isEdgeCommitted(srcPid, liveEdge.targetPid)
        ) {
          deletePromotedEdge(srcPid);
        }
      }
    }
  }

  function hasInFlightTicketForPid(parcelId) {
    for (const t of activeBatchTickets) {
      if (t.pid === parcelId) return true;
    }
    return false;
  }

  function syncUnconfirmedStandaloneBatchServerTs(
    targetPid,
    targetRec,
    { includeOwnTickets = false } = {}
  ) {
    if (
      !targetRec ||
      targetRec.confirmedServerTs !== null ||
      promotedEdgeBySourcePid.has(targetPid) ||
      hasUncommittedIncomingPromotedEdge(targetPid)
    ) {
      return;
    }
    const batch = targetRec.pendingBatch;
    if (!includeOwnTickets) {
      if (batch && !getBatchReplaceParcelId(batch) && !hasInFlightTicketForPid(targetPid)) {
        targetRec.serverTs = batch.baselineExpectedTs;
      }
      return;
    }
    let earliestBaselineTs = null;
    for (const t of activeBatchTickets) {
      if (t.pid !== targetPid) continue;
      if (t.batch && getBatchReplaceParcelId(t.batch)) return;
      if (earliestBaselineTs === null) {
        earliestBaselineTs = t.batch?.baselineExpectedTs ?? "";
      }
    }
    if (batch) {
      if (getBatchReplaceParcelId(batch)) return;
      if (earliestBaselineTs === null) {
        earliestBaselineTs = batch.baselineExpectedTs ?? "";
      }
    }
    if (earliestBaselineTs !== null) {
      targetRec.serverTs = earliestBaselineTs;
    }
  }

  function isParcelLocallyActive(parcelId) {
    if (
      recordsByParcelId.get(parcelId)?.pendingBatch ||
      hasUncommittedIncomingPromotedEdge(parcelId)
    ) {
      return true;
    }
    return hasInFlightTicketForPid(parcelId);
  }

  // If `srcPid`'s direct committed target is locally active without an outgoing edge
  // (`!promotedEdgeBySourcePid.has(target)`), step past it to its downstream committed target so
  // `srcPid` does not reattach onto an intermediate parcel that is actively detaching itself.
  function resolveRestorableCommittedTarget(srcPid) {
    let target = committedLineageByPid.get(srcPid)?.targetPid || "";
    const seen = new Set([srcPid]);
    while (
      target &&
      !seen.has(target) &&
      !promotedEdgeBySourcePid.has(target) &&
      isParcelLocallyActive(target) &&
      committedLineageByPid.get(target)?.targetPid
    ) {
      seen.add(target);
      target = committedLineageByPid.get(target).targetPid;
    }
    return target;
  }

  // Restore server-committed edges evicted during rollback (or cleared earlier by `promoteParcelKey`
  // or standalone `enqueue`) whenever the source parcel is locally idle and restoring is cycle-free.
  function restoreMissingCommittedEdges(excludePid = "") {
    let restoredCount = 0;
    for (const [srcPid, committedEntry] of committedLineageByPid.entries()) {
      if (
        srcPid === excludePid ||
        promotedEdgeBySourcePid.has(srcPid) ||
        isParcelLocallyActive(srcPid)
      ) {
        continue;
      }
      const committedTarget = resolveRestorableCommittedTarget(srcPid);
      if (
        committedTarget &&
        committedTarget !== srcPid &&
        !promotionPathReaches(committedTarget, srcPid)
      ) {
        setPromotedEdge(
          srcPid,
          committedTarget,
          committedEntry?.seq || COMMITTED_EDGE_SEQ,
          new Set()
        );
        restoredCount += 1;
      }
    }
    return restoredCount;
  }

  function rollbackUncommittedLineageEdges(rootTarget) {
    const uncommittedSources = [];
    for (const [srcPid, edge] of promotedEdgeBySourcePid.entries()) {
      if ((resolveLatestTargetPid(srcPid) || srcPid) === rootTarget) {
        if (!isEdgeCommitted(srcPid, edge.targetPid)) {
          uncommittedSources.push(srcPid);
        }
      }
    }
    for (const srcPid of uncommittedSources) {
      deletePromotedEdge(srcPid);
    }
    restoreMissingCommittedEdges();
  }

  function hasAnyCoordinatorState() {
    return (
      activeBatchTickets.size > 0 ||
      recordsByParcelId.size > 0 ||
      promotedEdgeBySourcePid.size > 0 ||
      committedLineageByPid.size > 0
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
    promotedEdgeBySourcePid.clear();
    committedLineageByPid.clear();
    _activeCoordinatorClearHooks.delete(clearAll);
  }

  function getServerTimestamp(parcelId) {
    const pid = String(parcelId || "").trim();
    return recordsByParcelId.get(pid)?.serverTs ?? "";
  }

  function clearRecordServerTs(
    targetPid,
    { clearConfirmed = true, clearVacatedFloor = true, clearBouncedFrom = true } = {}
  ) {
    const targetRec = targetPid ? recordsByParcelId.get(targetPid) : null;
    if (!targetRec) return;
    targetRec.serverTs = null;
    if (clearBouncedFrom) {
      targetRec.bouncedFromPid = "";
    }
    if (clearConfirmed) {
      targetRec.confirmedServerTs = null;
    }
    if (clearVacatedFloor) {
      targetRec.vacatedFloorTs = "";
    }
  }

  function advanceRecordServerTs(targetPid, rawTs, { force = false, confirmed = true } = {}) {
    if (!targetPid) return;
    const cleanTs = String(rawTs || "").trim();
    const targetRec = getOrCreateRecord(targetPid);
    if (
      force ||
      !targetRec.serverTs ||
      (cleanTs && cleanTs >= targetRec.serverTs)
    ) {
      targetRec.serverTs = cleanTs;
      if (confirmed && cleanTs) {
        targetRec.confirmedServerTs = cleanTs;
        targetRec.vacatedFloorTs = "";
      }
    }
  }

  function vacateRecordServerTs(
    targetPid,
    vacatingSrvTs,
    { keepIncomingProvisional = false } = {}
  ) {
    if (!targetPid) return;
    const cleanVacatingTs = String(vacatingSrvTs || "").trim();
    const targetRec = getOrCreateRecord(targetPid);
    targetRec.confirmedServerTs = null;
    if (
      cleanVacatingTs &&
      (!targetRec.vacatedFloorTs || cleanVacatingTs >= targetRec.vacatedFloorTs)
    ) {
      targetRec.vacatedFloorTs = cleanVacatingTs;
    }
    let hasStandaloneBatchOnTarget = Boolean(
      targetRec.pendingBatch && !getBatchReplaceParcelId(targetRec.pendingBatch)
    );
    if (targetRec.pendingBatch && !getBatchReplaceParcelId(targetRec.pendingBatch)) {
      targetRec.pendingBatch.baselineExpectedTs = "";
    }
    for (const t of activeBatchTickets) {
      if (t.pid === targetPid && t.batch && !getBatchReplaceParcelId(t.batch)) {
        hasStandaloneBatchOnTarget = true;
        t.batch.baselineExpectedTs = "";
      }
    }
    if (keepIncomingProvisional && !hasStandaloneBatchOnTarget && targetRec.serverTs !== null) {
      return;
    }
    targetRec.serverTs = "";
  }

  function reconcileIdleRecordTimestamps() {
    for (const [recPid, candRec] of recordsByParcelId.entries()) {
      const liveOutEdge = promotedEdgeBySourcePid.get(recPid);
      const hasCommittedOutEdge = Boolean(
        liveOutEdge && isEdgeCommitted(recPid, liveOutEdge.targetPid)
      );
      // When `recPid` already committed an outgoing hop, `hasPendingOrInFlight(recPid)` projects
      // onto the downstream target and would block clearing stale provisional timestamps on idle
      // vacated sources whenever the committed successor has pending edits.
      if (
        (liveOutEdge && !hasCommittedOutEdge) ||
        hasUncommittedIncomingPromotedEdge(recPid) ||
        (hasCommittedOutEdge ? isParcelLocallyActive(recPid) : hasPendingOrInFlight(recPid))
      ) {
        syncUnconfirmedStandaloneBatchServerTs(recPid, candRec, { includeOwnTickets: true });
        continue;
      }
      if (candRec.confirmedServerTs === null) {
        if (hasCommittedOutEdge) {
          candRec.serverTs = "";
        } else {
          clearRecordServerTs(recPid, {
            clearConfirmed: false,
            clearVacatedFloor: false,
            clearBouncedFrom: false,
          });
        }
      } else {
        advanceRecordServerTs(recPid, candRec.confirmedServerTs, {
          force: true,
          confirmed: false,
        });
      }
    }
  }

  function reconcileCommittedBatchTimestamps(pid, srvPid, srvTs, lineageRoot, batch) {
    // Only mark a source parcel vacated if its committed lineage still points forward at
    // `seq <= batch.causalSeq`; detached parcels that already committed a newer standalone
    // tombstone (`targetPid === ""`) keep their own confirmed row and timestamp untouched.
    const vacatedByBatch = new Set();
    for (const candVacated of [
      pid,
      getBatchReplaceParcelId(batch),
      ...batch.lineageEdges.map((e) => e.srcPid),
    ]) {
      const committedEntry = candVacated ? committedLineageByPid.get(candVacated) : null;
      if (
        candVacated &&
        candVacated !== srvPid &&
        (candVacated === pid ||
          (Boolean(committedEntry?.targetPid) && (committedEntry.seq || 0) <= batch.causalSeq))
      ) {
        vacatedByBatch.add(candVacated);
      }
    }
    const targetKeys = new Set([
      srvPid,
      pid,
      resolveLatestTargetPid(srvPid),
      lineageRoot,
      ...vacatedByBatch,
    ]);
    for (const candPid of recordsByParcelId.keys()) {
      if ((resolveLatestTargetPid(candPid) || candPid) === lineageRoot) {
        targetKeys.add(candPid);
      }
    }
    for (const keyPid of targetKeys) {
      if (!keyPid) continue;
      if (vacatedByBatch.has(keyPid)) {
        // Keep existing incoming provisional baselines (e.g. concurrent `Z -> A`) on vacated
        // parcels while resetting unpromoted vacated parcels to `""` (`vacatedFloorTs` blocks stale pulls).
        vacateRecordServerTs(keyPid, srvTs, {
          keepIncomingProvisional: hasIncomingPromotedEdge(keyPid),
        });
        continue;
      }
      // Do not re-confirm `srvPid` if a higher-seq downstream promotion already vacated it,
      // and do not overwrite an already-confirmed target's own row timestamp (or an incoming
      // provisional baseline on an already-vacated `srvPid`).
      const srvAlreadyVacated =
        keyPid === srvPid && Boolean(committedLineageByPid.get(srvPid)?.targetPid);
      if (srvAlreadyVacated && hasIncomingPromotedEdge(srvPid)) {
        continue;
      }
      if (keyPid === srvPid || (recordsByParcelId.get(keyPid)?.confirmedServerTs ?? null) === null) {
        advanceRecordServerTs(keyPid, srvTs, { confirmed: keyPid === srvPid && !srvAlreadyVacated });
      }
    }
  }

  function deriveSurvivingBatchBaselineTs(candBatch) {
    if (!candBatch) return "";
    const repPid = getBatchReplaceParcelId(candBatch);
    if (repPid) {
      if (!hasServerBackedRow(repPid)) return "";
      const repRec = recordsByParcelId.get(repPid);
      return repRec?.confirmedServerTs ?? repRec?.serverTs ?? "";
    }
    let survivingTs = "";
    if (Array.isArray(candBatch.lineageEdges)) {
      for (const edge of candBatch.lineageEdges) {
        const srcRec = recordsByParcelId.get(edge.srcPid);
        const srcTs = hasServerBackedRow(edge.srcPid)
          ? srcRec?.confirmedServerTs ?? srcRec?.serverTs ?? ""
          : "";
        if (srcTs && (!survivingTs || srcTs > survivingTs)) {
          survivingTs = srcTs;
        }
      }
    }
    return survivingTs;
  }

  function reconcileConflictTimestamps(pid, batch, currentDecision) {
    const conflictPid = String(currentDecision?.parcel_id || "").trim();
    const conflictTs = String(currentDecision?.decided_at || "").trim();
    if (conflictTs) {
      advanceRecordServerTs(conflictPid || pid, conflictTs, { force: true, confirmed: true });
      return;
    }
    const clearedKeys = new Set(
      [
        pid,
        conflictPid,
        getBatchReplaceParcelId(batch),
        ...batch.lineageEdges.map((e) => e.srcPid),
      ].filter(Boolean)
    );
    for (const clearKey of clearedKeys) {
      clearRecordServerTs(clearKey);
    }
    if (conflictPid) {
      // Blank-timestamp 409s leave `confirmedServerTs` null; record `hasServerRow` without
      // advancing `seq` (and bypass upstream committed pointers when clearing `prevTarget`) so
      // `hasServerBackedRow` holds while concurrent promotions and multi-hop chains stay valid.
      const prevCommitted = committedLineageByPid.get(conflictPid);
      const prevTarget = prevCommitted?.targetPid || "";
      if (!prevTarget || (prevCommitted?.seq || 0) <= (batch?.causalSeq || 0)) {
        if (prevTarget) {
          for (const [upPid, upEntry] of committedLineageByPid.entries()) {
            if (
              upEntry.targetPid === conflictPid &&
              upPid !== prevTarget &&
              !committedPathReaches(prevTarget, upPid)
            ) {
              committedLineageByPid.set(upPid, {
                targetPid: prevTarget,
                seq: Math.max(upEntry.seq || 0, prevCommitted?.seq || 0),
                hasServerRow: false,
              });
            }
          }
        }
        committedLineageByPid.set(conflictPid, {
          targetPid: "",
          seq: prevCommitted?.seq ?? COMMITTED_EDGE_SEQ,
          hasServerRow: true,
        });
      }
    }
    const batchTouchesClearedKey = (candPid, candBatch) => {
      if (!candBatch) return false;
      const repPid = getBatchReplaceParcelId(candBatch);
      return (
        clearedKeys.has(candPid) ||
        Boolean(repPid && clearedKeys.has(repPid)) ||
        Boolean(candBatch.lineageEdges?.some((e) => clearedKeys.has(e.srcPid)))
      );
    };
    for (const t of activeBatchTickets) {
      const tRec = recordsByParcelId.get(t.pid);
      if ((tRec?.confirmedServerTs ?? null) === null && batchTouchesClearedKey(t.pid, t.batch)) {
        const nextBaseTs = deriveSurvivingBatchBaselineTs(t.batch);
        t.batch.baselineExpectedTs = nextBaseTs;
        if (tRec && !tRec.pendingBatch) {
          tRec.serverTs = nextBaseTs;
        }
      }
    }
    for (const [candPid, candRec] of recordsByParcelId.entries()) {
      if (candRec.confirmedServerTs !== null) continue;
      if (batchTouchesClearedKey(candPid, candRec.pendingBatch)) {
        const nextBaseTs = deriveSurvivingBatchBaselineTs(candRec.pendingBatch);
        candRec.pendingBatch.baselineExpectedTs = nextBaseTs;
        candRec.serverTs = nextBaseTs;
      } else if (
        !candRec.pendingBatch &&
        !hasInFlightTicketForPid(candPid) &&
        Array.from(clearedKeys).some((k) => promotionPathReaches(k, candPid))
      ) {
        candRec.serverTs = "";
      }
    }
  }

  function resolveBatchExpectedServerTs(pid, rec, batch) {
    // A confirmed server row on `pid` is authoritative over any provisional incoming source baseline
    // because `Code.cjs` scans `Decisions` and matches `DEC:<pid>` before `replace_parcel_id`.
    if (rec.confirmedServerTs !== null) {
      return rec.confirmedServerTs;
    }
    const lookupReplacePid = getBatchReplaceParcelId(batch);
    if (isReplacePidSuperseded(batch, pid, lookupReplacePid)) {
      return "";
    }
    const baseTs = rec.serverTs ? rec.serverTs : batch.baselineExpectedTs;
    // When `pid` has no confirmed server row yet, bridge commits or baselines from `lookupReplacePid`
    // (including when an in-flight standalone save on `lookupReplacePid` settled after detaching).
    const replaceRec =
      lookupReplacePid && lookupReplacePid !== pid
        ? recordsByParcelId.get(lookupReplacePid)
        : null;
    if (replaceRec && replaceRec.serverTs !== null) {
      if (replaceRec.confirmedServerTs !== null) {
        return replaceRec.confirmedServerTs;
      }
      if (replaceRec.serverTs && (!baseTs || replaceRec.serverTs >= baseTs)) {
        return replaceRec.serverTs;
      }
    }
    return baseTs;
  }

  function recordHydratedTimestamp(parcelId, decidedAt, { fromCloud = false } = {}) {
    const pid = String(parcelId || "").trim();
    if (!pid) return;
    const incTs = String(decidedAt || "").trim();
    const rec = getOrCreateRecord(pid);
    if (fromCloud) {
      if (
        !hasPendingOrInFlight(pid) &&
        (!rec.vacatedFloorTs || (incTs && incTs > rec.vacatedFloorTs))
      ) {
        advanceRecordServerTs(pid, incTs, { confirmed: Boolean(incTs) });
      }
    } else if (
      rec.serverTs === null &&
      incTs &&
      (!rec.vacatedFloorTs || incTs > rec.vacatedFloorTs)
    ) {
      advanceRecordServerTs(pid, incTs, { force: true });
    }
    syncClearHookRegistration();
  }

  function captureBaselineExpectedTs(parcelId, fallbackDecidedAt = "") {
    const pid = String(parcelId || "").trim();
    if (!pid) return "";
    const rec = getOrCreateRecord(pid);
    if (rec.serverTs === null) {
      const rawFallback = String(fallbackDecidedAt || "").trim();
      const cleanFallback =
        rec.vacatedFloorTs && rawFallback && rawFallback <= rec.vacatedFloorTs ? "" : rawFallback;
      advanceRecordServerTs(pid, cleanFallback, {
        force: true,
        confirmed: Boolean(cleanFallback),
      });
    }
    syncClearHookRegistration();
    return rec.serverTs ?? "";
  }

  function releaseBatchTokenFromEdges(batchToken, shouldInspect = () => true) {
    if (!batchToken) return;
    for (const [srcPid, liveEdge] of promotedEdgeBySourcePid.entries()) {
      if (
        shouldInspect(srcPid, liveEdge) &&
        liveEdge.ownerTokens.delete(batchToken) &&
        liveEdge.ownerTokens.size === 0 &&
        !isEdgeCommitted(srcPid, liveEdge.targetPid)
      ) {
        deletePromotedEdge(srcPid);
      }
    }
  }

  function isCancelledAncestorBounce(
    fromPid,
    toPid,
    preDetachFromAncestors,
    effectiveTargetReplacePid,
    allowedToken,
    abandonedBounceNodes = null
  ) {
    if (!preDetachFromAncestors.has(toPid)) {
      return false;
    }
    // `upsert_parcel_decision` accepts only one `replace_parcel_id`: when `toPid` is already retiring
    // another upstream parcel, `fromPid -> toPid` cannot also retire `fromPid` and must be cancelled.
    if (effectiveTargetReplacePid && effectiveTargetReplacePid !== fromPid) {
      return true;
    }
    if (!hasServerBackedRow(fromPid)) {
      const origin = resolveUncommittedUpstreamOrigin(fromPid, toPid, allowedToken, {
        blockedPids: abandonedBounceNodes,
      });
      return !origin || origin === toPid || !hasServerBackedRow(origin);
    }
    return false;
  }

  function resolveSurvivingOwnerRewindTarget(srcPid, ownerTokens, fromPid) {
    const ownerBatches = [];
    for (const t of activeBatchTickets) {
      if (t.batch?.batchToken && ownerTokens.has(t.batch.batchToken)) {
        ownerBatches.push({ batch: t.batch, pid: t.pid, seq: t.batch.causalSeq || 0 });
      }
    }
    for (const [recPid, rec] of recordsByParcelId.entries()) {
      if (rec.pendingBatch?.batchToken && ownerTokens.has(rec.pendingBatch.batchToken)) {
        ownerBatches.push({
          batch: rec.pendingBatch,
          pid: recPid,
          seq: rec.pendingBatch.causalSeq || 0,
        });
      }
    }
    ownerBatches.sort((a, b) => b.seq - a.seq);
    let cur = srcPid;
    let rewindTarget = "";
    const seen = new Set([srcPid]);
    while (cur) {
      let nextHop = "";
      for (const { batch, pid: batchPid } of ownerBatches) {
        const hop = batch.lineageEdges?.find((e) => e.srcPid === cur);
        const cand = hop?.targetPid || (getBatchReplaceParcelId(batch) === cur ? batchPid : "");
        if (!cand || seen.has(cand)) continue;
        if (cand === fromPid) {
          nextHop = fromPid;
          break;
        }
        if (!nextHop) nextHop = cand;
      }
      if (!nextHop) break;
      rewindTarget = nextHop;
      if (rewindTarget === fromPid) {
        break;
      }
      seen.add(rewindTarget);
      cur = rewindTarget;
    }
    return rewindTarget && rewindTarget !== srcPid && !promotionPathReaches(rewindTarget, srcPid)
      ? rewindTarget
      : "";
  }

  function scrubRedirectedPendingBatches(
    fromPid,
    toPid,
    preDetachFromAncestors = null,
    abandonedBounceNodes = null
  ) {
    // Scrub superseded `fromPid` lineage/replace references from unsent pending batches on other
    // roots (including when `fromPid` briefly detached via `enqueue(fromPid)` or `toPid` detached
    // from `fromPid` before retargeting), and release their ownership tokens on upstream edges.
    const redirectedRoot = resolveLatestTargetPid(toPid) || toPid;
    let scrubbedAny = false;
    for (const [candPid, candRec] of recordsByParcelId.entries()) {
      if (candPid === toPid || !candRec.pendingBatch) continue;
      if ((resolveLatestTargetPid(candPid) || candPid) === redirectedRoot) continue;
      const candRepPid = getBatchReplaceParcelId(candRec.pendingBatch);
      const touchesFrom = Boolean(candRec.pendingBatch.touchedPids?.has(fromPid));
      const repRedirected = Boolean(
        candRepPid &&
          (promotionPathReaches(candRepPid, fromPid) || preDetachFromAncestors?.has(candRepPid))
      );
      const lineageRedirected = Boolean(
        touchesFrom &&
          !promotionPathReaches(fromPid, candPid) &&
          candRec.pendingBatch.lineageEdges?.some(
            (e) =>
              e.srcPid === fromPid ||
              e.targetPid === fromPid ||
              preDetachFromAncestors?.has(e.srcPid)
          )
      );
      if (!touchesFrom && !repRedirected) continue;
      scrubbedAny = true;
      if (candRec.pendingBatch.batchToken) {
        const candBranchNodes = new Set([candPid, ...(candRec.pendingBatch.touchedPids || [])]);
        for (const [nodePid] of promotedEdgeBySourcePid.entries()) {
          if (promotionPathReaches(nodePid, candPid)) {
            candBranchNodes.add(nodePid);
          }
        }
        for (const [srcPid, edge] of promotedEdgeBySourcePid.entries()) {
          const isAbandonedBounceHop = Boolean(
            abandonedBounceNodes?.has(srcPid) && srcPid !== fromPid
          );
          if (!isAbandonedBounceHop && promotionPathReaches(srcPid, redirectedRoot)) {
            if (
              edge.ownerTokens.delete(candRec.pendingBatch.batchToken) &&
              edge.ownerTokens.size === 0 &&
              !isEdgeCommitted(srcPid, edge.targetPid)
            ) {
              edge.ownerTokens.add(UNCLAIMED_EDGE);
            }
          } else if (
            (repRedirected ||
              (lineageRedirected &&
                (isAbandonedBounceHop || !promotionPathReaches(srcPid, candPid)))) &&
            edge.ownerTokens.delete(candRec.pendingBatch.batchToken) &&
            !isEdgeCommitted(srcPid, edge.targetPid)
          ) {
            if (edge.ownerTokens.size === 0) {
              deletePromotedEdge(srcPid);
            } else if (candBranchNodes.has(edge.targetPid) && srcPid !== fromPid) {
              // Rewind composite bypass edges (`srcPid -> candBranch` co-owned by an in-flight
              // upstream batch) back to that surviving owner's hop target (e.g. `fromPid` or `LOT_Y`).
              const rewindTarget = resolveSurvivingOwnerRewindTarget(
                srcPid,
                edge.ownerTokens,
                fromPid
              );
              if (rewindTarget) {
                edge.targetPid = rewindTarget;
              }
            }
          }
        }
      }
      if (repRedirected) {
        candRec.pendingBatch.lineageEdges = [];
        candRec.pendingBatch.bouncedFromPid = "";
        candRec.pendingBatch.rootReplacePid = "";
        candRec.pendingBatch.replaceCausalSeq = 0;
        syncPatchReplaceParcelId(candRec.pendingBatch.patchPayload, "");
        if (candRec.confirmedServerTs === null) {
          candRec.pendingBatch.baselineExpectedTs = "";
          candRec.serverTs = "";
        }
      } else if (lineageRedirected && Array.isArray(candRec.pendingBatch.lineageEdges)) {
        candRec.pendingBatch.lineageEdges = candRec.pendingBatch.lineageEdges.filter(
          (e) =>
            promotionPathReaches(e.srcPid, candPid) &&
            e.srcPid !== fromPid &&
            !preDetachFromAncestors?.has(e.srcPid)
        );
      }
      restampPendingBatch(candPid, candRec.pendingBatch);
      if (!repRedirected && lineageRedirected && candRec.confirmedServerTs === null) {
        const survivingTs = deriveSurvivingBatchBaselineTs(candRec.pendingBatch);
        candRec.pendingBatch.baselineExpectedTs = survivingTs;
        candRec.serverTs = survivingTs;
      }
    }
    return scrubbedAny;
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
    const preDetachFromAncestors = new Set([fromPid]);
    for (const candSrc of promotedEdgeBySourcePid.keys()) {
      if (promotionPathReaches(candSrc, fromPid)) {
        preDetachFromAncestors.add(candSrc);
      }
    }
    // Track `toPid` plus all reversed bounce ancestors not reaching `toPid` (including side
    // branches into abandoned intermediates) so bounces cannot adopt their `replace_parcel_id`.
    const abandonedBounceNodes = new Set();
    if (preDetachFromAncestors.has(toPid)) {
      abandonedBounceNodes.add(toPid);
      for (const ancPid of preDetachFromAncestors) {
        if (!promotionPathReaches(ancPid, toPid)) {
          abandonedBounceNodes.add(ancPid);
        }
      }
    }
    // Seed `toPid`'s provisional expected timestamp from `fromPid` only when `toPid` has no
    // confirmed server row yet or a committed/in-flight/non-bouncing outgoing promotion is
    // vacating `toPid` (preserving `toRec.confirmedServerTs` and `toRec.serverTs` on bounce-backs).
    const prevToOut = promotedEdgeBySourcePid.get(toPid);
    const toInFlightVacating = Array.from(activeBatchTickets).some(
      (t) =>
        t.pid !== toPid &&
        t.batch &&
        (getBatchReplaceParcelId(t.batch) === toPid ||
          t.batch.lineageEdges?.some((e) => e.srcPid === toPid))
    );
    const toVacatingOnServer = Boolean(
      prevToOut &&
        (isEdgeCommitted(toPid, prevToOut.targetPid) ||
          toInFlightVacating ||
          (!prevToOut.ownerTokens.has(UNCLAIMED_EDGE) && !preDetachFromAncestors.has(toPid)))
    );
    if (toVacatingOnServer || toRec.confirmedServerTs === null) {
      advanceRecordServerTs(toPid, baseTs, { force: true, confirmed: false });
    }
    const prevFromTarget = promotedEdgeBySourcePid.get(fromPid)?.targetPid || "";
    fromRec.bouncedFromPid = "";
    toRec.bouncedFromPid = "";
    detachOutgoingPromotedEdge(toPid, fromPid);
    const edgeSeq = _nextCausalSeq++;
    setPromotedEdge(fromPid, toPid, edgeSeq);
    if (fromRec.timerId !== null) {
      cancelTimer(fromRec.timerId);
      fromRec.timerId = null;
    }
    let anyMergedRepSuperseded = false;
    let evictedBounceHop = false;
    const hasPriorFromBatch = Boolean(fromRec.pendingBatch);
    if (hasPriorFromBatch) {
      const priorFrom = fromRec.pendingBatch;
      fromRec.pendingBatch = null;
      const existingTo = toRec.pendingBatch;
      const {
        bouncedFromPid,
        rootReplacePid,
        replaceCausalSeq,
        prevRepSuperseded: fromRepSuperseded,
      } = resolveBatchReplaceLineage(
        fromPid,
        toPid,
        priorFrom,
        fromPid,
        edgeSeq,
        abandonedBounceNodes
      );
      if (fromRepSuperseded) {
        evictBatchUncommittedEdges(priorFrom);
      }
      const cancelledBounce = isCancelledAncestorBounce(
        fromPid,
        toPid,
        preDetachFromAncestors,
        rootReplacePid,
        priorFrom.batchToken,
        abandonedBounceNodes
      );
      const fromRepOverridden = Boolean(
        !fromRepSuperseded &&
          !cancelledBounce &&
          rootReplacePid &&
          (getBatchReplaceParcelId(priorFrom) === toPid || preDetachFromAncestors.has(toPid)) &&
          rootReplacePid !== toPid
      );
      if (fromRepOverridden) {
        releaseBatchTokenFromEdges(
          priorFrom.batchToken,
          (srcPid) =>
            srcPid !== fromPid &&
            (abandonedBounceNodes.has(srcPid) || !promotionPathReaches(rootReplacePid, srcPid))
        );
      }
      const effectiveBouncedFromPid = cancelledBounce ? fromPid : bouncedFromPid;
      if (cancelledBounce || (!rootReplacePid && effectiveBouncedFromPid)) {
        evictedBounceHop = true;
        toRec.bouncedFromPid = effectiveBouncedFromPid;
        deletePromotedEdge(fromPid);
        releaseBatchTokenFromEdges(
          priorFrom.batchToken,
          (srcPid) => !promotionPathReaches(srcPid, toPid)
        );
      }
      const existingToRep = resolveBatchReplaceLineage(toPid, toPid, existingTo, "", 0);
      if (existingToRep.prevRepSuperseded) {
        evictBatchUncommittedEdges(existingTo);
      }
      anyMergedRepSuperseded =
        fromRepSuperseded || fromRepOverridden || existingToRep.prevRepSuperseded;
      const nextPatch = {
        ...priorFrom.patchPayload,
        ...(existingTo ? existingTo.patchPayload : {}),
      };
      syncPatchReplaceParcelId(nextPatch, rootReplacePid);
      const batchToken = existingTo ? existingTo.batchToken : priorFrom.batchToken;
      if (existingTo && priorFrom.batchToken && priorFrom.batchToken !== batchToken) {
        for (const edge of promotedEdgeBySourcePid.values()) {
          if (edge.ownerTokens.delete(priorFrom.batchToken)) {
            edge.ownerTokens.add(batchToken);
          }
        }
      }
      let retainedFromEdges = [];
      if (!fromRepSuperseded && Array.isArray(priorFrom.lineageEdges)) {
        if (evictedBounceHop) {
          retainedFromEdges = priorFrom.lineageEdges.filter(
            (e) => e.srcPid !== toPid && !abandonedBounceNodes.has(e.srcPid)
          );
        } else if (fromRepOverridden) {
          retainedFromEdges = priorFrom.lineageEdges.filter(
            (e) =>
              e.srcPid === fromPid ||
              (promotionPathReaches(rootReplacePid, e.srcPid) &&
                !abandonedBounceNodes.has(e.srcPid))
          );
        } else {
          retainedFromEdges = priorFrom.lineageEdges;
        }
      }
      const mergedPriorEdges = [
        ...retainedFromEdges,
        ...(!existingToRep.prevRepSuperseded && Array.isArray(existingTo?.lineageEdges)
          ? existingTo.lineageEdges
          : []),
      ];
      const recomputeEvictedUncreatedBaseline = Boolean(
        evictedBounceHop &&
          (!existingTo || existingToRep.prevRepSuperseded) &&
          toRec.confirmedServerTs === null
      );
      const survivingEvictedBaseline = recomputeEvictedUncreatedBaseline
        ? deriveSurvivingBatchBaselineTs({ rootReplacePid, lineageEdges: mergedPriorEdges })
        : "";
      if (recomputeEvictedUncreatedBaseline) {
        toRec.serverTs = survivingEvictedBaseline;
      }
      const rawMergedBaseline =
        existingTo && !existingToRep.prevRepSuperseded
          ? existingTo.baselineExpectedTs
          : recomputeEvictedUncreatedBaseline
          ? survivingEvictedBaseline
          : !fromRepSuperseded
          ? priorFrom.baselineExpectedTs
          : baseTs;
      toRec.pendingBatch = {
        baselineExpectedTs: clampVacatedBaselineTs(toPid, rootReplacePid, rawMergedBaseline),
        bouncedFromPid: effectiveBouncedFromPid,
        rootReplacePid,
        replaceCausalSeq,
        sessionEpoch: existingTo ? existingTo.sessionEpoch : priorFrom.sessionEpoch,
        ...captureBatchLineageStamp(toPid, batchToken, {
          priorLineageEdges: mergedPriorEdges,
          rootReplacePid,
          patchReplacePid: nextPatch.replace_parcel_id,
        }),
        patchPayload: nextPatch,
      };
    } else if (toRec.pendingBatch) {
      restampPendingBatch(toPid, toRec.pendingBatch);
    }
    const scrubbedAny = scrubRedirectedPendingBatches(
      fromPid,
      toPid,
      preDetachFromAncestors,
      abandonedBounceNodes
    );
    // Must run after `scrubRedirectedPendingBatches` downgrades downstream pending-batch tokens
    // back to `UNCLAIMED_EDGE` so empty-batch intermediate bounces (`A -> B -> C`, then `B -> A`)
    // can inspect and evict claimable upstream uncommitted edges reaching `fromPid`.
    if (!hasPriorFromBatch && preDetachFromAncestors.has(toPid)) {
      const targetUpstreamOrigin = resolveUncommittedUpstreamOrigin(
        toPid,
        fromPid,
        toRec.pendingBatch?.batchToken,
        { skipStartServerRow: true }
      );
      const effectiveTargetReplacePid =
        getBatchReplaceParcelId(toRec.pendingBatch) ||
        (targetUpstreamOrigin && targetUpstreamOrigin !== toPid ? targetUpstreamOrigin : "");
      const cancelledEmptyBounce = isCancelledAncestorBounce(
        fromPid,
        toPid,
        preDetachFromAncestors,
        effectiveTargetReplacePid,
        toRec.pendingBatch?.batchToken,
        abandonedBounceNodes
      );
      if (cancelledEmptyBounce) {
        evictedBounceHop = true;
        toRec.bouncedFromPid = fromPid;
        if (toRec.pendingBatch) {
          toRec.pendingBatch.bouncedFromPid = fromPid;
        }
        deletePromotedEdge(fromPid);
      }
      for (const [srcPid, edge] of promotedEdgeBySourcePid.entries()) {
        const shouldEvictAbandoned = cancelledEmptyBounce
          ? !promotionPathReaches(srcPid, toPid) &&
            (abandonedBounceNodes.has(srcPid) || promotionPathReaches(srcPid, fromPid))
          : srcPid !== fromPid && abandonedBounceNodes.has(srcPid);
        if (
          shouldEvictAbandoned &&
          !isEdgeCommitted(srcPid, edge.targetPid) &&
          isUncommittedEdgeClaimableBy(edge, toRec.pendingBatch?.batchToken)
        ) {
          deletePromotedEdge(srcPid);
          evictedBounceHop = true;
        }
      }
    }
    if (
      scrubbedAny ||
      anyMergedRepSuperseded ||
      evictedBounceHop ||
      (prevFromTarget && prevFromTarget !== toPid)
    ) {
      const restoredCount = restoreMissingCommittedEdges(evictedBounceHop ? "" : fromPid);
      if (scrubbedAny || restoredCount > 0) {
        restampPendingBatch(toPid, toRec.pendingBatch);
      }
      reconcileIdleRecordTimestamps();
    }
    syncClearHookRegistration();
    return toRec.serverTs ?? "";
  }

  function enqueue(parcelId, patchPayload = {}, baselineExpectedTs = "") {
    const pid = String(parcelId || "").trim();
    if (!pid || typeof sendDecisionRpc !== "function") return;
    const rec = getOrCreateRecord(pid);
    const prev = rec.pendingBatch;
    const mergedPatch = prev ? { ...prev.patchPayload, ...patchPayload } : { ...patchPayload };
    const {
      bouncedFromPid,
      rootReplacePid,
      replaceCausalSeq,
      prevRepSuperseded,
    } = resolveBatchReplaceLineage(
      pid,
      pid,
      prev,
      patchPayload.replace_parcel_id,
      _nextCausalSeq
    );
    rec.bouncedFromPid = bouncedFromPid;
    // Preserve the pre-debounce server lookup PK across chained <650ms CAMPUS:<SLUG> renames,
    // while clearing any `replace_parcel_id` carried over from `prev` once superseded or self-targeted.
    syncPatchReplaceParcelId(mergedPatch, rootReplacePid);
    const detachedPrevOut = detachOutgoingPromotedEdge(pid);
    const batchSessionEpoch = _authSessionEpoch;
    const batchToken = prev ? prev.batchToken : Symbol("parcelBatch");
    const cleanBaselineTs = clampVacatedBaselineTs(pid, rootReplacePid, baselineExpectedTs);
    if (prevRepSuperseded) {
      evictBatchUncommittedEdges(prev);
      restoreMissingCommittedEdges(pid);
      if (
        !rootReplacePid &&
        rec.confirmedServerTs === null &&
        !hasUncommittedIncomingPromotedEdge(pid)
      ) {
        rec.serverTs = cleanBaselineTs;
      }
    }
    rec.pendingBatch = {
      baselineExpectedTs:
        prev && !prevRepSuperseded ? prev.baselineExpectedTs : cleanBaselineTs,
      bouncedFromPid,
      rootReplacePid,
      replaceCausalSeq,
      sessionEpoch: batchSessionEpoch,
      ...captureBatchLineageStamp(pid, batchToken, {
        priorLineageEdges: !prevRepSuperseded ? prev?.lineageEdges : null,
        rootReplacePid,
        patchReplacePid: mergedPatch.replace_parcel_id,
      }),
      patchPayload: mergedPatch,
    };
    syncUnconfirmedStandaloneBatchServerTs(pid, rec);
    if (detachedPrevOut) {
      // Run after assigning `rec.pendingBatch` so `isParcelLocallyActive(pid)` is already true when
      // `resolveRestorableCommittedTarget` checks whether `pid` is actively detaching standalone.
      if (restoreMissingCommittedEdges(pid) > 0) {
        restampPendingBatch(pid, rec.pendingBatch);
      }
      reconcileIdleRecordTimestamps();
    }
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
        batch,
        touchedPids: batch.touchedPids,
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
      let batchSettled = false;
      const settleCompletedBatch = (committedOk) => {
        if (batchSettled) return;
        batchSettled = true;
        batchTicket.finish();
        if (capturedGen === generation) {
          if (!committedOk) {
            evictBatchUncommittedEdges(batch);
          }
          restoreMissingCommittedEdges();
          reconcileIdleRecordTimestamps();
          syncClearHookRegistration();
        }
      };

      const sendRpc = async () => {
        let res;
        try {
          if (isBatchCancelled()) {
            return { ok: false, cancelled: true };
          }
          while (true) {
            const blocking = getBlockingTicketPromise(
              pid,
              batchTicket.seq,
              batchTicket.touchedPids
            );
            if (!blocking) break;
            try {
              await blocking;
            } catch (_e) {}
            if (isBatchCancelled()) {
              return { ok: false, cancelled: true };
            }
          }
          const lookupReplacePid = getBatchReplaceParcelId(batch);
          const omitReplacePid = isReplacePidSuperseded(batch, pid, lookupReplacePid);
          const wirePatch =
            omitReplacePid && batch.patchPayload?.replace_parcel_id
              ? (({ replace_parcel_id: _omitted, ...rest }) => rest)(batch.patchPayload)
              : batch.patchPayload;
          const effectiveBatch = omitReplacePid
            ? { ...batch, rootReplacePid: "", patchPayload: wirePatch, lineageEdges: [] }
            : batch;
          const expectedTs = resolveBatchExpectedServerTs(pid, rec, batch);
          res = await sendDecisionRpc({
            parcelId: pid,
            expectedUpdatedAt: expectedTs,
            patch: wirePatch,
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
            if (omitReplacePid) {
              evictBatchUncommittedEdges(batch);
            }
            const lineageRoot = resolveLatestTargetPid(pid) || pid;
            commitLineageEdgesBeforeSeq(
              pid,
              srvPid,
              batch.causalSeq,
              effectiveBatch.lineageEdges,
              batch.batchToken
            );
            reconcileCommittedBatchTimestamps(pid, srvPid, srvTs, lineageRoot, effectiveBatch);
            const latestTargetPid = resolveLatestTargetPid(pid);
            const isSuperseded = Boolean(latestTargetPid && latestTargetPid !== pid);
            const ownFollowUpQueued = hasNewerOverlappingBatch(
              pid,
              batch.causalSeq,
              batchTicket.seq
            );
            settleCompletedBatch(true);
            if (typeof onSuccess === "function") {
              onSuccess(res, {
                hasNewerPendingEdit: isSuperseded || ownFollowUpQueued,
                supersededByParcelId: isSuperseded ? latestTargetPid : "",
              });
            }
          } else if (res && res.conflict) {
            reconcileConflictTimestamps(pid, effectiveBatch, res.currentDecision);
            settleCompletedBatch(false);
            if (typeof onConflict === "function") {
              onConflict(res);
            }
          }
          return res;
        } finally {
          const committedOk = Boolean(
            !isBatchCancelled() && res && res.ok && res.decision && res.decision.parcel_id
          );
          const stayQueuedForRetry = Boolean(res && res.queuedForRetry && !isBatchCancelled());
          if (!stayQueuedForRetry) {
            settleCompletedBatch(committedOk);
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
            if (candRec.pendingBatch) {
              evictBatchUncommittedEdges(candRec.pendingBatch);
              candRec.pendingBatch = null;
            }
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
        // Must run after `t.finish()` removes cancelled tickets from `activeBatchTickets` so
        // `!isParcelLocallyActive(srcPid)` can restore server-committed edges on evicted parcels.
        for (const t of toCancel) {
          evictBatchUncommittedEdges(t.batch);
        }
        rollbackUncommittedLineageEdges(rootTarget);
        reconcileIdleRecordTimestamps();
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
