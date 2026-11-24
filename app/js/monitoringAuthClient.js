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

  const recordsByParcelId = new Map();
  const promotedEdgeBySourcePid = new Map();
  const committedLineageByPid = new Map();
  const activeBatchTickets = new Set();
  let generation = 0;

  function setPromotedEdge(srcPid, targetPid, seq, ownerToken = null) {
    promotedEdgeBySourcePid.set(srcPid, { targetPid, seq, ownerToken });
  }

  function deletePromotedEdge(srcPid) {
    promotedEdgeBySourcePid.delete(srcPid);
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
    for (const [srcPid, dstPid] of lineageEdges) {
      if (srcPid) touched.add(srcPid);
      if (dstPid) touched.add(dstPid);
    }
    return touched;
  }

  // Snapshot and claim pre-batch hops at enqueue/promote time so concurrent in-flight
  // re-promotions cannot erase committed hops, bridge unsent hops, or leak foreign/failed
  // uncommitted hops across detached intermediate parcels.
  function claimAndSnapshotLineageEdges(
    targetPid,
    maxCausalSeq,
    batchToken,
    inheritedTokens = null
  ) {
    const canUseEdge = (srcPid, edge) => {
      const committedTarget = committedLineageByPid.get(srcPid)?.targetPid || "";
      return (
        committedTarget === edge.targetPid ||
        edge.ownerToken === null ||
        edge.ownerToken === batchToken ||
        Boolean(inheritedTokens && inheritedTokens.has(edge.ownerToken))
      );
    };
    const snapshot = [];
    for (const [srcPid, edge] of promotedEdgeBySourcePid.entries()) {
      if (
        edge.seq < maxCausalSeq &&
        canUseEdge(srcPid, edge) &&
        promotionPathReaches(edge.targetPid, targetPid, maxCausalSeq, canUseEdge)
      ) {
        const committedTarget = committedLineageByPid.get(srcPid)?.targetPid || "";
        if (committedTarget !== edge.targetPid) {
          edge.ownerToken = batchToken;
        }
        snapshot.push([srcPid, edge.targetPid, edge.seq]);
      }
    }
    return snapshot;
  }

  function captureBatchLineageStamp(
    targetPid,
    batchToken,
    { inheritedTokens = null, rootReplacePid = "", patchReplacePid = "" } = {}
  ) {
    const causalSeq = _nextCausalSeq++;
    const lineageEdges = claimAndSnapshotLineageEdges(
      targetPid,
      causalSeq,
      batchToken,
      inheritedTokens
    );
    return {
      batchToken,
      causalSeq,
      lineageEdges,
      touchedPids: buildTouchedPids(targetPid, lineageEdges, rootReplacePid, patchReplacePid),
    };
  }

  function getPromotedTarget(parcelId) {
    return resolveLatestTargetPid(parcelId);
  }

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

  function setsIntersect(setA, setB) {
    if (!setA || !setB) return false;
    for (const item of setA) {
      if (setB.has(item)) return true;
    }
    return false;
  }

  function hasPendingOrInFlight(parcelId) {
    const pid = String(parcelId || "").trim();
    if (!pid) return false;
    const targetPid = resolveLatestTargetPid(pid) || pid;
    if (hasOwnActive(pid) || hasOwnActive(targetPid)) return true;
    for (const t of activeBatchTickets) {
      if (t.touchedPids && (t.touchedPids.has(pid) || t.touchedPids.has(targetPid))) {
        return true;
      }
    }
    for (const candRec of recordsByParcelId.values()) {
      if (candRec.pendingBatch?.touchedPids?.has(pid)) {
        return true;
      }
    }
    for (const srcPid of promotedEdgeBySourcePid.keys()) {
      if ((resolveLatestTargetPid(srcPid) || srcPid) === targetPid && hasOwnActive(srcPid)) {
        return true;
      }
    }
    return false;
  }

  function getBlockingTicketPromise(pid, maxSeq = Infinity, candidatePids = null) {
    const targetPid = resolveLatestTargetPid(pid) || pid;
    for (const t of activeBatchTickets) {
      if (t.seq >= maxSeq) continue;
      if ((resolveLatestTargetPid(t.pid) || t.pid) === targetPid) {
        return t.promise;
      }
      if (t.touchedPids) {
        if (t.touchedPids.has(pid) || t.touchedPids.has(targetPid)) {
          return t.promise;
        }
        if (setsIntersect(candidatePids, t.touchedPids)) {
          return t.promise;
        }
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

  function commitLineageEdgesBeforeSeq(
    pid,
    srvPid,
    maxCausalSeq,
    batchLineageEdges = [],
    batchToken = null
  ) {
    const eligibleBySrc = new Map();
    for (const [srcPid, targetPid] of batchLineageEdges) {
      eligibleBySrc.set(srcPid, targetPid);
    }
    if (srvPid && srvPid !== pid) {
      for (const [srcPid, targetPid] of claimAndSnapshotLineageEdges(
        srvPid,
        maxCausalSeq,
        batchToken
      )) {
        eligibleBySrc.set(srcPid, targetPid);
      }
    }
    eligibleBySrc.delete(srvPid);
    const canAdvanceCommitted = (nodePid) =>
      (committedLineageByPid.get(nodePid)?.seq || 0) < maxCausalSeq;
    const nextTargetByPid = new Map();
    for (const [srcPid, targetPid] of eligibleBySrc.entries()) {
      if (srcPid && targetPid && srcPid !== targetPid && canAdvanceCommitted(srcPid)) {
        nextTargetByPid.set(srcPid, targetPid);
      }
    }
    // Retain `{ targetPid: "", seq: maxCausalSeq }` tombstones on terminal roots rather than
    // deleting entries so lower-seq out-of-order commits cannot resurrect cleared outgoing edges.
    for (const rootPid of new Set([srvPid, pid])) {
      if (!rootPid || eligibleBySrc.has(rootPid)) continue;
      const outEdge = promotedEdgeBySourcePid.get(rootPid);
      if ((!outEdge || outEdge.seq >= maxCausalSeq) && canAdvanceCommitted(rootPid)) {
        nextTargetByPid.set(rootPid, "");
      }
    }
    // Clear stale outgoing targets for all updated nodes before checking cycle-freedom:
    // `Map` iterates in insertion order rather than topological order, so updating in a
    // single pass would reject reversed hops (e.g. B->A->C after prior A->B) as false cycles
    // against pre-batch committed edges that this commit is overwriting.
    for (const nodePid of nextTargetByPid.keys()) {
      committedLineageByPid.set(nodePid, { targetPid: "", seq: maxCausalSeq });
    }
    for (const [srcPid, targetPid] of nextTargetByPid.entries()) {
      if (targetPid && !committedPathReaches(targetPid, srcPid)) {
        committedLineageByPid.set(srcPid, { targetPid, seq: maxCausalSeq });
        const liveEdge = promotedEdgeBySourcePid.get(srcPid);
        if (liveEdge && liveEdge.targetPid === targetPid) {
          liveEdge.ownerToken = null;
        }
      }
    }
  }

  function evictBatchUncommittedEdges(batch) {
    if (!batch || !Array.isArray(batch.lineageEdges)) return;
    for (const [srcPid, targetPid, edgeSeq] of batch.lineageEdges) {
      const liveEdge = promotedEdgeBySourcePid.get(srcPid);
      const committedTarget = committedLineageByPid.get(srcPid)?.targetPid || "";
      if (
        liveEdge &&
        liveEdge.seq === edgeSeq &&
        liveEdge.targetPid === targetPid &&
        committedTarget !== targetPid
      ) {
        deletePromotedEdge(srcPid);
      }
    }
  }

  // Restore server-committed edges evicted during rollback (or cleared earlier by `promoteParcelKey`
  // or standalone `enqueue`) whenever the source parcel is idle and restoring keeps the graph cycle-free.
  function restoreMissingCommittedEdges() {
    for (const [srcPid, { targetPid: committedTarget }] of committedLineageByPid.entries()) {
      if (
        committedTarget &&
        committedTarget !== srcPid &&
        !promotedEdgeBySourcePid.has(srcPid) &&
        !hasPendingOrInFlight(srcPid) &&
        !promotionPathReaches(committedTarget, srcPid)
      ) {
        setPromotedEdge(srcPid, committedTarget, COMMITTED_EDGE_SEQ, null);
      }
    }
  }

  function rollbackUncommittedLineageEdges(rootTarget) {
    const uncommittedSources = [];
    for (const [srcPid, edge] of promotedEdgeBySourcePid.entries()) {
      if ((resolveLatestTargetPid(srcPid) || srcPid) === rootTarget) {
        const committedTarget = committedLineageByPid.get(srcPid)?.targetPid || "";
        if (committedTarget !== edge.targetPid) {
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
    deletePromotedEdge(toPid);
    setPromotedEdge(fromPid, toPid, _nextCausalSeq++);
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
      const batchToken = existingTo ? existingTo.batchToken : priorFrom.batchToken;
      const inheritedTokens = new Set([priorFrom.batchToken]);
      if (existingTo?.batchToken) inheritedTokens.add(existingTo.batchToken);
      toRec.pendingBatch = {
        baselineExpectedTs: existingTo ? existingTo.baselineExpectedTs : priorFrom.baselineExpectedTs,
        rootReplacePid,
        sessionEpoch: existingTo ? existingTo.sessionEpoch : priorFrom.sessionEpoch,
        ...captureBatchLineageStamp(toPid, batchToken, {
          inheritedTokens,
          rootReplacePid,
          patchReplacePid: nextPatch.replace_parcel_id,
        }),
        patchPayload: nextPatch,
      };
    } else if (toRec.pendingBatch) {
      Object.assign(
        toRec.pendingBatch,
        captureBatchLineageStamp(toPid, toRec.pendingBatch.batchToken, {
          rootReplacePid: toRec.pendingBatch.rootReplacePid,
          patchReplacePid: toRec.pendingBatch.patchPayload?.replace_parcel_id,
        })
      );
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
    deletePromotedEdge(pid);
    const batchSessionEpoch = _authSessionEpoch;
    const batchToken = prev ? prev.batchToken : Symbol("parcelBatch");
    rec.pendingBatch = {
      baselineExpectedTs: prev ? prev.baselineExpectedTs : String(baselineExpectedTs || "").trim(),
      rootReplacePid,
      sessionEpoch: batchSessionEpoch,
      ...captureBatchLineageStamp(pid, batchToken, {
        rootReplacePid,
        patchReplacePid: mergedPatch.replace_parcel_id,
      }),
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
      const finishBatchTicket = () => batchTicket.finish();

      const sendRpc = async () => {
        if (isBatchCancelled()) {
          finishBatchTicket();
          return { ok: false, cancelled: true };
        }
        for (
          let blocking = getBlockingTicketPromise(
            pid,
            batchTicket.seq,
            batchTicket.touchedPids
          );
          blocking;
          blocking = getBlockingTicketPromise(
            pid,
            batchTicket.seq,
            batchTicket.touchedPids
          )
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
            commitLineageEdgesBeforeSeq(
              pid,
              srvPid,
              batch.causalSeq,
              batch.lineageEdges,
              batch.batchToken
            );
            const targetKeys = new Set([
              srvPid,
              pid,
              resolveLatestTargetPid(srvPid),
              lineageRoot,
            ]);
            for (const [srcPid, dstPid] of batch.lineageEdges) {
              targetKeys.add(srcPid);
              targetKeys.add(dstPid);
            }
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
          const committedOk = Boolean(
            !isBatchCancelled() && res && res.ok && res.decision && res.decision.parcel_id
          );
          const stayQueuedForRetry = Boolean(res && res.queuedForRetry && !isBatchCancelled());
          if (!stayQueuedForRetry) {
            finishBatchTicket();
            if (!committedOk && capturedGen === generation) {
              evictBatchUncommittedEdges(batch);
              restoreMissingCommittedEdges();
              syncClearHookRegistration();
            }
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
        // `!hasPendingOrInFlight(srcPid)` can restore server-committed edges on evicted parcels.
        for (const t of toCancel) {
          evictBatchUncommittedEdges(t.batch);
        }
        rollbackUncommittedLineageEdges(rootTarget);
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
