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
  if (!tok) return true;
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

export function readActiveMonitoringSession({
  localStore = getSafeStorage("local"),
  sessionStore = getSafeStorage("session"),
} = {}) {
  for (const store of [sessionStore, localStore]) {
    if (!store) continue;
    try {
      const rawStr = store.getItem(LS_ADMIN_SESSION_KEY);
      if (rawStr) {
        const parsed = normalizeMonitoringSession(JSON.parse(rawStr));
        if (parsed) return parsed;
      }
    } catch (_e) {}
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
export function purgePrivateMonitoringStorage({
  preserveEmail = "",
  localStore = getSafeStorage("local"),
} = {}) {
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
  const sess = readActiveMonitoringSession(opts);
  if (!sess) {
    purgePrivateMonitoringStorage(opts);
    return null;
  }
  // Even when signed in, scrub pre-Phase-1 un-namespaced keys so all private state lives strictly in role-checked cache
  purgePrivateMonitoringStorage({ ...opts, preserveEmail: sess.email });
  return sess;
}

export function saveCachedMonitoringState(
  email,
  role,
  rawState = {},
  { localStore = getSafeStorage("local") } = {}
) {
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

export function loadCachedMonitoringState(
  email,
  expectedRole,
  { localStore = getSafeStorage("local") } = {}
) {
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

function broadcastAuthEvent(eventType, meta = {}, localStore = getSafeStorage("local")) {
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

export function signOutMonitoringSession({
  localStore = getSafeStorage("local"),
  sessionStore = getSafeStorage("session"),
} = {}) {
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
  clearLegacyMirrorOnSignOut(localStore);
  _pendingAuthRetryQueue.length = 0;
  broadcastAuthEvent("sign_out", { role: "public", email: "" }, localStore);
}

export async function verifyAndSignInMonitoring({
  webhookUrl = DEFAULT_SHEET_WEBHOOK_URL,
  googleIdToken = "",
  email = "",
  passkey = "",
  fetchImpl = null,
  localStore = getSafeStorage("local"),
  sessionStore = getSafeStorage("session"),
} = {}) {
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
    purgePrivateMonitoringStorage({ localStore });
    clearLegacyMirrorOnSignOut(localStore);
  } else {
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

export function queuePendingAuthRetry(retryFn, description = "") {
  if (typeof retryFn === "function") {
    _pendingAuthRetryQueue.push({ retryFn, description, queuedAt: Date.now() });
  }
  return _pendingAuthRetryQueue.length;
}

export function getPendingAuthRetryCount() {
  return _pendingAuthRetryQueue.length;
}

export async function replayPendingAuthRetries() {
  const pending = _pendingAuthRetryQueue.splice(0, _pendingAuthRetryQueue.length);
  const results = [];
  for (const item of pending) {
    try {
      results.push({ ok: true, result: await item.retryFn(), description: item.description });
    } catch (err) {
      results.push({ ok: false, error: err?.message || String(err), description: item.description });
    }
  }
  return results;
}

/**
 * Execute an authenticated Apps Script `doPost` RPC with JWT `exp` preflight detection
 * so 1-hour Google ID token expiry pauses cleanly for 1-click re-auth without losing dirty edits.
 */
export async function callMonitoringRpc({
  webhookUrl = DEFAULT_SHEET_WEBHOOK_URL,
  action,
  body = {},
  session = null,
  fetchImpl = null,
  onTokenExpired = null,
  retryFn = null,
} = {}) {
  const activeSession = session || readActiveMonitoringSession();
  if (!activeSession || !activeSession.authorized) {
    return { ok: false, authorized: false, error: "Sign-in required." };
  }

  if (isSessionTokenExpired(activeSession)) {
    if (typeof retryFn === "function") {
      queuePendingAuthRetry(retryFn, action);
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
      if (typeof retryFn === "function") {
        queuePendingAuthRetry(retryFn, action);
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

export async function pullCloudMonitoringState({
  webhookUrl = DEFAULT_SHEET_WEBHOOK_URL,
  session = null,
  fetchImpl = null,
  localStore = getSafeStorage("local"),
  onTokenExpired = null,
} = {}) {
  const activeSession = session || readActiveMonitoringSession({ localStore });
  if (!activeSession || !activeSession.authorized) {
    return { ok: false, authorized: false, signedOut: true };
  }
  const resp = await callMonitoringRpc({
    webhookUrl,
    action: "pull_monitoring_state",
    session: activeSession,
    fetchImpl,
    onTokenExpired,
  });
  if (resp && resp.ok) {
    saveCachedMonitoringState(activeSession.email, resp.role || activeSession.role, resp, {
      localStore,
    });
  }
  return resp;
}

/**
 * Subscribe to cross-tab `storage` events so Sign Out or `Staff -> Screener` switches
 * in a sibling browser tab immediately unmount private DOM nodes and memory caches.
 */
export function subscribeCrossTabAuthChanges(onAuthChanged, targetWindow = null) {
  const win = targetWindow || (typeof window !== "undefined" ? window : null);
  if (!win || typeof win.addEventListener !== "function") {
    return () => {};
  }
  const handler = (ev) => {
    if (!ev) return;
    if (ev.key === LS_ADMIN_SESSION_KEY || ev.key === LS_CROSS_TAB_BROADCAST_KEY || ev.key === null) {
      const currentSession = readActiveMonitoringSession();
      if (!currentSession) {
        purgePrivateMonitoringStorage();
      }
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

export function resolveConfiguredGoogleClientId({
  explicitClientId = "",
  localStore = getSafeStorage("local"),
} = {}) {
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

export async function mountGoogleSignInButton(
  containerEl,
  { clientId = "", onCredential = null } = {}
) {
  const resolvedClientId = resolveConfiguredGoogleClientId({ explicitClientId: clientId });
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
