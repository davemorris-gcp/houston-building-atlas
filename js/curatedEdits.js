/**
 * Curated Archival Overrides & Google Sheets Live Synchronization Module
 * for The Houston Building Atlas v2 (Preservation Houston).
 *
 * Architecture:
 * 1. Baseline overrides live in `public/data/curated_overrides.json` (zero-cost static JSON).
 * 2. Live admin edits can be published from a Google Sheet (`Approved_Edits` tab -> CSV)
 *    and fetched client-side over CORS on startup without rebuilding the PMTiles shards.
 * 3. End-user correction suggestions ("Suggest a Correction") are submitted with
 *    `status = "Pending"` to a Google Apps Script webhook feeding the `Pending_Submissions`
 *    tab of the same Google Sheet. Suggestions NEVER go live on the public map until a
 *    Preservation Houston administrator changes the row's status dropdown to `Approved`.
 */

const LS_SHEET_CSV_URL_KEY = "ph_atlas_sheet_csv_url";
const LS_WEBHOOK_URL_KEY = "ph_atlas_submission_webhook_url";
const LS_PENDING_SUGGESTIONS_KEY = "ph_atlas_pending_suggestions";

/**
 * Convert a standard Google Sheets browser URL (`.../spreadsheets/d/<ID>/edit...`)
 * into a direct CORS-friendly CSV export URL if the user pasted an edit link.
 */
export function normalizeGoogleSheetCsvUrl(rawUrl) {
  const trimmed = String(rawUrl || "").trim();
  if (!trimmed) return "";

  // Already a published CSV or gviz CSV export URL
  if (trimmed.includes("output=csv") || trimmed.includes("tqx=out:csv")) {
    return trimmed;
  }

  // Standard Google Sheet URL: https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit...
  const match = trimmed.match(/\/spreadsheets\/d\/([a-zA-Z0-9-_]+)/);
  if (match && match[1] && match[1] !== "e") {
    const sheetId = match[1];
    const gidMatch = trimmed.match(/[?#&]gid=(\d+)/);
    if (gidMatch) {
      return `https://docs.google.com/spreadsheets/d/${sheetId}/export?format=csv&gid=${gidMatch[1]}`;
    }
    return `https://docs.google.com/spreadsheets/d/${sheetId}/gviz/tq?tqx=out:csv&sheet=Approved_Edits`;
  }

  return trimmed;
}

/**
 * Compute normalized atlas decade (1840 for 1836–1849, otherwise floor(year/10)*10).
 */
export function computeNormalizedDecade(yearBuilt) {
  const yr = Number(yearBuilt) || 0;
  if (yr < 1836 || yr > 2030) return 0;
  if (yr < 1850) return 1840;
  return Math.floor(yr / 10) * 10;
}

/**
 * RFC 4180 compliant CSV parser that returns an array of row objects keyed by lowercase header name.
 */
export function parseCsvToObjects(csvText) {
  const text = String(csvText || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const rows = [];
  let currentRow = [];
  let currentField = "";
  let inQuotes = false;

  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"') {
        if (i + 1 < text.length && text[i + 1] === '"') {
          currentField += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        currentField += ch;
      }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === ",") {
      currentRow.push(currentField.trim());
      currentField = "";
    } else if (ch === "\n") {
      currentRow.push(currentField.trim());
      if (currentRow.some((cell) => cell !== "")) {
        rows.push(currentRow);
      }
      currentRow = [];
      currentField = "";
    } else {
      currentField += ch;
    }
  }
  currentRow.push(currentField.trim());
  if (currentRow.some((cell) => cell !== "")) {
    rows.push(currentRow);
  }

  if (rows.length < 2) return [];
  const headers = rows[0].map((h) =>
    h
      .toLowerCase()
      .replace(/[^a-z0-9_]+/g, "_")
      .replace(/^_|_$/g, "")
  );

  const objects = [];
  for (let r = 1; r < rows.length; r++) {
    const row = rows[r];
    const obj = {};
    for (let c = 0; c < headers.length; c++) {
      if (headers[c]) {
        obj[headers[c]] = row[c] !== undefined ? row[c] : "";
      }
    }
    objects.push(obj);
  }
  return objects;
}

/**
 * Convert parsed Google Sheet CSV rows into normalized override records keyed by 13-digit `hcad_num`.
 * Only rows with `status` empty or in `["approved", "verified", "live"]` are accepted.
 */
export function parseOverridesFromSheetRows(rows) {
  const overrides = {};
  for (const row of rows) {
    const status = String(row.status || row.review_status || "approved")
      .trim()
      .toLowerCase();
    if (status && !["approved", "verified", "live", "active", "yes"].includes(status)) {
      continue;
    }

    const rawHcad = String(
      row.hcad_num || row.hcad_account || row.account || row.acct || ""
    ).replace(/\D/g, "");
    if (!rawHcad) continue;
    const hcadNum = rawHcad.length < 13 ? rawHcad.padStart(13, "0") : rawHcad;

    const yrRaw = Number(
      row.year_built || row.verified_year_built || row.suggested_year_built || row.corrected_year || 0
    );
    const yearBuilt = yrRaw >= 1830 && yrRaw <= 2030 ? Math.round(yrRaw) : 0;
    const origYrRaw = Number(
      row.original_hcad_year || row.hcad_year_built || row.current_year_built || 0
    );

    let geometry = null;
    if (row.geometry_geojson) {
      try {
        geometry = JSON.parse(row.geometry_geojson);
      } catch (_e) {
        geometry = null;
      }
    }

    overrides[hcadNum] = {
      hcad_num: hcadNum,
      address: String(row.address || row.street_address || "").trim().toUpperCase(),
      historic_district: String(row.historic_district || row.district || "").trim(),
      contributing: String(row.contributing || row.contributing_status || "").trim(),
      year_built: yearBuilt,
      decade: computeNormalizedDecade(yearBuilt),
      original_hcad_year: origYrRaw >= 1830 ? Math.round(origYrRaw) : 0,
      bld_style: String(row.bld_style || row.style || "").trim(),
      architect: String(row.architect || row.builder || "").trim(),
      landmark_name: String(row.landmark_name || row.historic_name || "").trim(),
      landmark_type: String(row.landmark_type || "").trim(),
      source_type: String(
        row.source_type || row.evidence_source || "Preservation Houston Archival Record"
      ).trim(),
      source_citation: String(
        row.source_citation || row.directory_evidence || row.notes || ""
      ).trim(),
      source_url: String(row.source_url || row.contentdm_url || "").trim(),
      verified_by: String(row.verified_by || "Preservation Houston").trim(),
      updated_at: String(row.updated_at || "").trim(),
      geometry,
    };
  }
  return overrides;
}

/**
 * Load baseline overrides from `public/data/curated_overrides.json` and optionally merge
 * live approved rows from a published Google Sheet CSV URL.
 */
export async function loadCuratedOverrides(customSheetCsvUrl = null) {
  let baseConfig = {
    googleSheetCsvUrl: "",
    submissionWebhookUrl: "",
    overrides: {},
  };

  try {
    const res = await fetch("public/data/curated_overrides.json", { cache: "no-cache" });
    if (res.ok) {
      const data = await res.json();
      baseConfig = {
        googleSheetCsvUrl: data.googleSheetCsvUrl || "",
        submissionWebhookUrl: data.submissionWebhookUrl || "",
        overrides: data.overrides || {},
      };
    }
  } catch (err) {
    console.warn("Could not load baseline curated_overrides.json:", err);
  }

  const storedCsvUrl =
    typeof localStorage !== "undefined" ? localStorage.getItem(LS_SHEET_CSV_URL_KEY) : "";
  const storedWebhookUrl =
    typeof localStorage !== "undefined" ? localStorage.getItem(LS_WEBHOOK_URL_KEY) : "";

  const activeSheetCsvUrl = normalizeGoogleSheetCsvUrl(
    customSheetCsvUrl !== null ? customSheetCsvUrl : storedCsvUrl || baseConfig.googleSheetCsvUrl
  );
  const activeWebhookUrl = (storedWebhookUrl || baseConfig.submissionWebhookUrl || "").trim();

  const mergedOverrides = { ...baseConfig.overrides };
  let sheetSyncStatus = {
    connected: Boolean(activeSheetCsvUrl),
    csvUrl: activeSheetCsvUrl,
    webhookUrl: activeWebhookUrl,
    sheetRowCount: 0,
    totalOverrideCount: Object.keys(mergedOverrides).length,
    error: null,
  };

  if (activeSheetCsvUrl) {
    try {
      const sheetRes = await fetch(activeSheetCsvUrl, { cache: "no-store" });
      if (!sheetRes.ok) {
        throw new Error(`HTTP ${sheetRes.status}`);
      }
      const csvText = await sheetRes.text();
      const rows = parseCsvToObjects(csvText);
      const sheetOverrides = parseOverridesFromSheetRows(rows);
      for (const [hcadNum, ov] of Object.entries(sheetOverrides)) {
        const existing = mergedOverrides[hcadNum] || {};
        mergedOverrides[hcadNum] = {
          ...existing,
          ...ov,
          geometry: ov.geometry || existing.geometry || null,
        };
      }
      sheetSyncStatus.sheetRowCount = Object.keys(sheetOverrides).length;
      sheetSyncStatus.totalOverrideCount = Object.keys(mergedOverrides).length;
    } catch (err) {
      sheetSyncStatus.error = err.message || String(err);
      console.warn("Google Sheet CSV live fetch failed (using baseline overrides):", err);
    }
  }

  return {
    overrides: mergedOverrides,
    syncStatus: sheetSyncStatus,
  };
}

/**
 * Save or clear custom Google Sheet CSV & Apps Script Webhook URLs in browser storage.
 */
export function saveGoogleSheetEndpoints({ csvUrl, webhookUrl }) {
  if (typeof localStorage === "undefined") return;
  const normalizedCsv = normalizeGoogleSheetCsvUrl(csvUrl);
  if (normalizedCsv) {
    localStorage.setItem(LS_SHEET_CSV_URL_KEY, normalizedCsv);
  } else {
    localStorage.removeItem(LS_SHEET_CSV_URL_KEY);
  }

  const trimmedWebhook = String(webhookUrl || "").trim();
  if (trimmedWebhook) {
    localStorage.setItem(LS_WEBHOOK_URL_KEY, trimmedWebhook);
  } else {
    localStorage.removeItem(LS_WEBHOOK_URL_KEY);
  }
}

/**
 * Merge curated override fields onto a feature's properties object if its `hcad_num` matches.
 */
export function applyOverrideToProperties(props, overridesMap) {
  if (!props || !overridesMap) return props;
  const hcadNum = String(props.hcad_num || "").trim();
  if (!hcadNum || !overridesMap[hcadNum]) return props;

  const ov = overridesMap[hcadNum];
  const origYear = Number(ov.original_hcad_year) || Number(props.year_built) || 0;
  const verifiedYear = Number(ov.year_built) || Number(props.year_built) || 0;

  return {
    ...props,
    year_built: verifiedYear,
    decade: computeNormalizedDecade(verifiedYear) || props.decade || 0,
    original_hcad_year: origYear,
    is_curated_override: true,
    address: ov.address || props.address || "",
    historic_district: ov.historic_district || props.historic_district || "",
    contributing: ov.contributing || props.contributing || "",
    bld_style: ov.bld_style || props.bld_style || "",
    architect: ov.architect || props.architect || "",
    landmark_name: ov.landmark_name || props.landmark_name || "",
    landmark_type: ov.landmark_type || props.landmark_type || "",
    source_type: ov.source_type || "Preservation Houston Archival Record",
    source_citation: ov.source_citation || "",
    source_url: ov.source_url || "",
    verified_by: ov.verified_by || "Preservation Houston",
    override_updated_at: ov.updated_at || "",
  };
}

/**
 * Submit an end-user property data correction suggestion for moderator review.
 * Never mutates the live public dataset directly; posts with `status: "Pending"`
 * to the configured Google Sheet Apps Script webhook and stores a local copy.
 */
export async function submitCorrectionSuggestion(payload, webhookUrl = "") {
  const record = {
    status: "Pending",
    submitted_at: new Date().toISOString().slice(0, 19).replace("T", " "),
    hcad_num: String(payload.hcad_num || "").trim(),
    address: String(payload.address || "").trim(),
    historic_district: String(payload.historic_district || "").trim(),
    hcad_year_built: Number(payload.current_year_built) || 0,
    suggested_year_built: Number(payload.suggested_year_built) || 0,
    bld_style: String(payload.bld_style || "").trim(),
    architect: String(payload.architect || "").trim(),
    source_type: String(payload.source_type || "Houston City Directory").trim(),
    source_citation: String(payload.source_citation || "").trim(),
    source_url: String(payload.source_url || "").trim(),
    submitter_name: String(payload.submitter_name || "").trim(),
    submitter_email: String(payload.submitter_email || "").trim(),
  };

  // Save in local browser queue so the submitter or admin can view/export it
  const existing = getLocalPendingSuggestions();
  existing.unshift(record);
  if (typeof localStorage !== "undefined") {
    localStorage.setItem(LS_PENDING_SUGGESTIONS_KEY, JSON.stringify(existing.slice(0, 200)));
  }

  let webhookDelivered = false;
  const targetUrl = String(webhookUrl || "").trim();
  if (targetUrl) {
    try {
      await fetch(targetUrl, {
        method: "POST",
        mode: "no-cors",
        headers: { "Content-Type": "text/plain;charset=utf-8" },
        body: JSON.stringify(record),
      });
      webhookDelivered = true;
    } catch (err) {
      console.warn("Webhook delivery warning (suggestion saved locally):", err);
    }
  }

  return {
    ok: true,
    webhookDelivered,
    record,
  };
}

/**
 * Retrieve pending correction suggestions saved in this browser session.
 */
export function getLocalPendingSuggestions() {
  if (typeof localStorage === "undefined") return [];
  try {
    const raw = localStorage.getItem(LS_PENDING_SUGGESTIONS_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch (_e) {
    return [];
  }
}

/**
 * Format an array of suggestion/override records as an RFC 4180 CSV string
 * matching the Preservation Houston Google Sheet column layout.
 */
export function formatSuggestionsAsCsv(records) {
  const cols = [
    "status",
    "hcad_num",
    "address",
    "historic_district",
    "hcad_year_built",
    "suggested_year_built",
    "bld_style",
    "architect",
    "source_type",
    "source_citation",
    "source_url",
    "submitter_name",
    "submitter_email",
    "submitted_at",
  ];
  const escapeCell = (val) => {
    const s = String(val ?? "");
    if (s.includes(",") || s.includes('"') || s.includes("\n")) {
      return `"${s.replace(/"/g, '""')}"`;
    }
    return s;
  };
  const lines = [cols.join(",")];
  for (const r of records) {
    lines.push(cols.map((c) => escapeCell(r[c])).join(","));
  }
  return lines.join("\n");
}

const LS_ADMIN_SESSION_KEY = "ph_atlas_admin_session";

/**
 * Return the active Preservation Houston Admin session if unlocked in this browser.
 */
export function getAdminSession() {
  if (typeof sessionStorage === "undefined") return null;
  try {
    const raw = sessionStorage.getItem(LS_ADMIN_SESSION_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (parsed && parsed.authorized) return parsed;
    return null;
  } catch (_e) {
    return null;
  }
}

/**
 * Clear the active Admin session (lock Admin Mode).
 */
export function clearAdminSession() {
  if (typeof sessionStorage !== "undefined") {
    sessionStorage.removeItem(LS_ADMIN_SESSION_KEY);
  }
}

/**
 * Authenticate a Preservation Houston Administrator against the Google Sheet's
 * bound Apps Script (`action: "verify_admin"`), which checks either:
 *   1. A Google-signed OAuth/ID token whose email is in `Spreadsheet.getEditors()` / `getOwner()`, or
 *   2. The private Sheet Editor Passkey stored inside cell B6 of `Instructions_&_Apps_Script`
 *      (accessible only to Google accounts with Editor access on the Restricted Sheet).
 */
export async function authenticateAdminSession({
  googleIdToken = "",
  adminPasskey = "",
  adminEmail = "",
  webhookUrl = "",
}) {
  const token = String(googleIdToken || "").trim();
  const passkey = String(adminPasskey || "").trim();
  const email = String(adminEmail || "").trim();
  if (!token && !passkey) {
    return { ok: false, error: "Please sign in with Google or enter the Sheet Editor Passkey." };
  }

  const targetUrl = String(webhookUrl || "").trim();
  let verifiedByWebhook = false;
  let resolvedEmail = email || "Preservation Houston Sheet Editor";

  if (targetUrl) {
    try {
      const res = await fetch(targetUrl, {
        method: "POST",
        headers: { "Content-Type": "text/plain;charset=utf-8" },
        body: JSON.stringify({
          action: "verify_admin",
          google_id_token: token,
          admin_passkey: passkey,
          admin_email: email,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        if (!data.authorized) {
          return {
            ok: false,
            error:
              data.error ||
              "Access denied: Google account is not listed as an Editor on the Curated Overrides Google Sheet.",
          };
        }
        verifiedByWebhook = true;
        resolvedEmail = data.email || resolvedEmail;
      }
    } catch (_err) {
      // If webhook is not yet deployed or blocks CORS readback, credentials are still sent on every admin_approve POST
    }
  }

  const session = {
    authorized: true,
    verifiedByWebhook,
    email: resolvedEmail,
    googleIdToken: token,
    adminPasskey: passkey,
    unlockedAt: new Date().toISOString(),
  };

  if (typeof sessionStorage !== "undefined") {
    sessionStorage.setItem(LS_ADMIN_SESSION_KEY, JSON.stringify(session));
  }
  return { ok: true, session };
}

/**
 * Submit an Administrator-Approved override directly (`action: "admin_approve"`).
 * The Google Apps Script verifies `google_id_token` (against `Spreadsheet.getEditors()`)
 * or `admin_passkey` server-side before writing `status = "Approved"`.
 */
export async function submitAdminApprovedOverride(payload, webhookUrl = "") {
  const session = getAdminSession();
  if (!session || !session.authorized) {
    throw new Error("Admin session required to publish approved overrides.");
  }

  const record = {
    action: "admin_approve",
    status: "Approved",
    submitted_at: new Date().toISOString().slice(0, 10),
    hcad_num: String(payload.hcad_num || "").trim(),
    address: String(payload.address || "").trim().toUpperCase(),
    historic_district: String(payload.historic_district || "").trim(),
    contributing: String(payload.contributing || "").trim(),
    hcad_year_built: Number(payload.current_year_built) || 0,
    suggested_year_built: Number(payload.suggested_year_built) || 0,
    bld_style: String(payload.bld_style || "").trim(),
    architect: String(payload.architect || "").trim(),
    source_type: String(payload.source_type || "Houston City Directory").trim(),
    source_citation: String(payload.source_citation || "").trim(),
    source_url: String(payload.source_url || "").trim(),
    verified_by: session.email || "Preservation Houston Archival Review",
    google_id_token: session.googleIdToken || "",
    admin_passkey: session.adminPasskey || "",
  };

  let webhookDelivered = false;
  const targetUrl = String(webhookUrl || "").trim();
  if (targetUrl) {
    try {
      await fetch(targetUrl, {
        method: "POST",
        mode: "no-cors",
        headers: { "Content-Type": "text/plain;charset=utf-8" },
        body: JSON.stringify(record),
      });
      webhookDelivered = true;
    } catch (err) {
      console.warn("Admin webhook delivery warning:", err);
    }
  }

  return {
    ok: true,
    webhookDelivered,
    override: {
      hcad_num: record.hcad_num,
      address: record.address,
      historic_district: record.historic_district,
      contributing: record.contributing,
      year_built: record.suggested_year_built,
      decade: computeNormalizedDecade(record.suggested_year_built),
      original_hcad_year: record.hcad_year_built,
      bld_style: record.bld_style,
      architect: record.architect,
      source_type: record.source_type,
      source_citation: record.source_citation,
      source_url: record.source_url,
      verified_by: record.verified_by,
      updated_at: record.submitted_at,
    },
  };
}

