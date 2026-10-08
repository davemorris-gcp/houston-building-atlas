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

    const rawIdField = String(
      row.building_id || row.hcad_num || row.hcad_account || row.account || row.acct || ""
    ).trim();
    const rawHcad = rawIdField.split("#")[0].replace(/\D/g, "");
    if (!rawHcad) continue;
    const hcadNum = rawHcad.length < 13 ? rawHcad.padStart(13, "0") : rawHcad;
    const hasBuildingSuffix = rawIdField.includes("#");
    const overrideKey = hasBuildingSuffix
      ? `${hcadNum}#${rawIdField.split("#").slice(1).join("#")}`
      : hcadNum;

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

    overrides[overrideKey] = {
      id: overrideKey,
      building_id: overrideKey,
      hcad_num: hcadNum,
      is_building_override: hasBuildingSuffix,
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

export function isObsoleteRestrictedSheetUrl(url) {
  const s = String(url || "").trim();
  if (!s) return false;
  // Once a Google Sheet is set to Restricted, direct /gviz/tq or /export URLs on the raw sheet ID
  // redirect to accounts.google.com/ServiceLogin and fail CORS; only /spreadsheets/d/e/2PACX-.../pub works.
  if (s.includes("/gviz/tq") || s.includes("1dpd9A5z4SmFkHut-fmBi8deIDGVZ8bDzYVdP88-_ZDs")) {
    return true;
  }
  return false;
}

/**
 * Load baseline overrides from `public/data/curated_overrides.json` and optionally merge
 * live approved rows from a published Google Sheet CSV URL (with automatic fallback to
 * the bound Google Apps Script `doGet` CSV endpoint).
 */
export async function loadCuratedOverrides(customSheetCsvUrl = null) {
  let baseConfig = {
    googleSheetCsvUrl: "",
    submissionWebhookUrl: "",
    overrides: {},
  };

  try {
    const res = await fetch("public/data/curated_overrides.json?v=20261008g", { cache: "no-store" });
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

  let storedCsvUrl =
    typeof localStorage !== "undefined" ? localStorage.getItem(LS_SHEET_CSV_URL_KEY) : "";
  if (storedCsvUrl && isObsoleteRestrictedSheetUrl(storedCsvUrl)) {
    if (typeof localStorage !== "undefined") {
      localStorage.removeItem(LS_SHEET_CSV_URL_KEY);
    }
    storedCsvUrl = "";
  }

  const storedWebhookUrl =
    typeof localStorage !== "undefined" ? localStorage.getItem(LS_WEBHOOK_URL_KEY) : "";

  const candidateCsvUrl =
    customSheetCsvUrl !== null && !isObsoleteRestrictedSheetUrl(customSheetCsvUrl)
      ? customSheetCsvUrl
      : storedCsvUrl || baseConfig.googleSheetCsvUrl;

  const activeSheetCsvUrl = normalizeGoogleSheetCsvUrl(candidateCsvUrl);
  const activeWebhookUrl = (storedWebhookUrl || baseConfig.submissionWebhookUrl || "").trim();

  const mergedOverrides = { ...baseConfig.overrides };
  let sheetSyncStatus = {
    connected: Boolean(activeSheetCsvUrl || activeWebhookUrl),
    csvUrl: activeSheetCsvUrl,
    webhookUrl: activeWebhookUrl,
    sheetRowCount: 0,
    totalOverrideCount: Object.keys(mergedOverrides).length,
    error: null,
  };

  const syncedHcads = new Set();
  const applyCsvText = (csvText) => {
    const rows = parseCsvToObjects(csvText);
    const sheetOverrides = parseOverridesFromSheetRows(rows);
    for (const [hcadNum, ov] of Object.entries(sheetOverrides)) {
      syncedHcads.add(hcadNum);
      const existing = mergedOverrides[hcadNum] || {};
      mergedOverrides[hcadNum] = {
        ...existing,
        ...ov,
        geometry: ov.geometry || existing.geometry || null,
        good_brick_awards: existing.good_brick_awards || null,
        good_brick_summary: existing.good_brick_summary || "",
      };
    }
    sheetSyncStatus.sheetRowCount = syncedHcads.size;
    sheetSyncStatus.totalOverrideCount = Object.keys(mergedOverrides).length;
  };

  // Fetch both the CDN-published CSV and the real-time Apps Script doGet endpoint in parallel.
  // Google's "Publish to the web" CSV caches for ~3-5 minutes, whereas the Apps Script doGet
  // reads `Approved_Edits` directly with 0-second latency the moment an editor changes a row to Approved.
  const fetchPromises = [];
  if (activeSheetCsvUrl) {
    fetchPromises.push(
      fetch(activeSheetCsvUrl, { cache: "no-store" }).then(async (r) => {
        if (!r.ok) throw new Error(`CSV HTTP ${r.status}`);
        return { source: "csv", text: await r.text() };
      })
    );
  }
  if (activeWebhookUrl) {
    fetchPromises.push(
      fetch(activeWebhookUrl, { cache: "no-store" }).then(async (r) => {
        if (!r.ok) throw new Error(`Webhook HTTP ${r.status}`);
        return { source: "webhook", text: await r.text() };
      })
    );
  }

  if (fetchPromises.length > 0) {
    const settled = await Promise.allSettled(fetchPromises);
    let anySucceeded = false;
    let firstError = null;
    for (const item of settled) {
      if (item.status === "fulfilled" && item.value && item.value.text) {
        applyCsvText(item.value.text);
        anySucceeded = true;
      } else if (item.status === "rejected" && !firstError) {
        firstError = item.reason;
      }
    }
    if (!anySucceeded && firstError) {
      sheetSyncStatus.error = firstError.message || String(firstError);
      console.warn("Google Sheet live sync failed (using baseline overrides):", firstError);
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
  if (normalizedCsv && !isObsoleteRestrictedSheetUrl(normalizedCsv)) {
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
 * Merge curated override fields onto a feature's properties object if its `id` (`building_id`)
 * or `hcad_num` matches.
 */
export function applyOverrideToProperties(props, overridesMap) {
  if (!props || !overridesMap) return props;
  const bldId = String(props.building_id || "").trim();
  const featId = String(props.id || "").trim();
  const hcadNum = String(props.hcad_num || "").trim();

  let ov = null;
  if (bldId && overridesMap[bldId]) {
    ov = overridesMap[bldId];
  } else if (featId && overridesMap[featId]) {
    ov = overridesMap[featId];
  } else if (
    hcadNum &&
    overridesMap[hcadNum] &&
    !overridesMap[hcadNum].is_building_override
  ) {
    ov = overridesMap[hcadNum];
  }
  if (!ov) return props;

  const origYear = Number(ov.original_hcad_year) || Number(props.year_built) || 0;
  const verifiedYear = Number(ov.year_built) || Number(props.year_built) || 0;
  const isGbPoint = Boolean(props.is_good_brick);

  return {
    ...props,
    id: isGbPoint ? props.id : ov.id || props.id || hcadNum,
    building_id: ov.building_id || ov.id || props.building_id || "",
    hcad_num: ov.hcad_num || hcadNum,
    year_built: verifiedYear,
    decade: computeNormalizedDecade(verifiedYear) || props.decade || 0,
    original_hcad_year: origYear,
    is_curated_override: Boolean(
      ov.source_type || ov.source_citation || ov.year_built || props.is_curated_override
    ),
    is_building_override: Boolean(ov.is_building_override),
    replace_parcel_shards: Boolean(ov.replace_parcel_shards),
    keep_shard_footprints: Boolean(ov.keep_shard_footprints),
    suppress_shard_hcads: Array.isArray(ov.suppress_shard_hcads)
      ? ov.suppress_shard_hcads
      : Array.isArray(props.suppress_shard_hcads)
      ? props.suppress_shard_hcads
      : [],
    use_category: ov.use_category || props.use_category || "Residential",
    stories: Number(ov.stories) || Number(props.stories) || 1,
    height_m: Number(ov.height_m) || Number(props.height_m) || 4.5,
    address: (isGbPoint && props.address) || ov.address || props.address || "",
    historic_district: ov.historic_district || props.historic_district || "",
    contributing: ov.contributing || props.contributing || "",
    bld_style: ov.bld_style || props.bld_style || "",
    architect: ov.architect || props.architect || "",
    landmark_name:
      (isGbPoint && props.landmark_name) || ov.landmark_name || props.landmark_name || "",
    landmark_type: ov.landmark_type || props.landmark_type || "",
    source_type: ov.source_type || props.source_type || "Preservation Houston Archival Record",
    source_citation: ov.source_citation || props.source_citation || "",
    source_url: ov.source_url || props.source_url || "",
    verified_by: ov.verified_by || props.verified_by || "Preservation Houston",
    override_updated_at: ov.updated_at || props.override_updated_at || "",
    good_brick_awards:
      (isGbPoint && props.good_brick_awards) ||
      ov.good_brick_awards ||
      props.good_brick_awards ||
      null,
    good_brick_summary:
      (isGbPoint && props.good_brick_summary) ||
      ov.good_brick_summary ||
      props.good_brick_summary ||
      "",
    landmark_report_url:
      ov.landmark_report_url ||
      props.landmark_report_url ||
      props.report_pdf_url ||
      "",
    landmark_code:
      ov.landmark_code ||
      props.landmark_code ||
      props.plm_num ||
      props.lm_num ||
      "",
    landmark_summary:
      ov.landmark_summary ||
      props.landmark_summary ||
      props.pdf_summary ||
      "",
  };
}

/**
 * Submit an end-user property data correction suggestion for moderator review.
 * Never mutates the live public dataset directly; posts with `status: "Pending"`
 * to the configured Google Sheet Apps Script webhook and stores a local copy.
 */
export async function submitCorrectionSuggestion(payload, webhookUrl = "") {
  const footprintIssue = String(payload.footprint_issue || "").trim();
  const footprintNotes = String(payload.footprint_notes || "").trim();
  const rawCitation = String(payload.source_citation || "").trim();
  const rawSourceType = String(payload.source_type || "Houston City Directory").trim();
  const satelliteUrl = String(payload.satellite_url || "").trim();

  const currentYr = Number(payload.current_year_built) || 0;
  const suggestedYr = Number(payload.suggested_year_built) || currentYr || 0;

  // Format source_type & source_citation so Footprint/Shape reports stand out clearly in the Google Sheet Pending_Submissions tab
  const effectiveSourceType = footprintIssue
    ? rawCitation && Number(payload.suggested_year_built) && Number(payload.suggested_year_built) !== currentYr
      ? `${rawSourceType} + Footprint Issue (${footprintIssue})`
      : `Footprint Issue: ${footprintIssue}`
    : rawSourceType;

  const citationParts = [];
  if (footprintIssue || footprintNotes) {
    citationParts.push(
      `[FOOTPRINT / SHAPE ISSUE — ${footprintIssue || "Geometry Error"}]: ${
        footprintNotes || "Flagged for building footprint review."
      }`
    );
  }
  if (rawCitation) {
    citationParts.push(rawCitation);
  }

  const record = {
    status: "Pending",
    submitted_at: new Date().toISOString().slice(0, 19).replace("T", " "),
    hcad_num: String(payload.hcad_num || "").trim(),
    address: String(payload.address || "").trim(),
    historic_district: String(payload.historic_district || "").trim(),
    hcad_year_built: currentYr,
    suggested_year_built: suggestedYr,
    bld_style: String(payload.bld_style || "").trim(),
    architect: String(payload.architect || "").trim(),
    source_type: effectiveSourceType,
    source_citation: citationParts.join(" | "),
    source_url: String(payload.source_url || satelliteUrl || "").trim(),
    footprint_issue: footprintIssue,
    footprint_notes: footprintNotes,
    photo_url: String(payload.photo_url || "").trim(),
    photo_year: payload.photo_year ? Number(payload.photo_year) || String(payload.photo_year).trim() : "",
    photo_caption: String(payload.photo_caption || "").trim(),
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
    "footprint_issue",
    "footprint_notes",
    "photo_url",
    "photo_year",
    "photo_caption",
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
  if (!token && (!email || !passkey)) {
    return {
      ok: false,
      error: "Please enter both your Sheet Editor Google Account Email and the Sheet Editor Passkey.",
    };
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
      if (!res.ok) {
        return { ok: false, error: `Verification webhook HTTP ${res.status}` };
      }
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
    } catch (err) {
      return {
        ok: false,
        error: `Could not reach Google Sheet verification service: ${err.message || err}`,
      };
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
 * or `admin_email` + `admin_passkey` server-side before writing `status = "Approved"`.
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
    admin_email: session.email || "",
    google_id_token: session.googleIdToken || "",
    admin_passkey: session.adminPasskey || "",
  };

  let webhookDelivered = false;
  const targetUrl = String(webhookUrl || "").trim();
  if (targetUrl) {
    const res = await fetch(targetUrl, {
      method: "POST",
      headers: { "Content-Type": "text/plain;charset=utf-8" },
      body: JSON.stringify(record),
    });
    if (res.ok) {
      const data = await res.json();
      if (!data.authorized) {
        throw new Error(data.error || "Server rejected admin approval credentials.");
      }
      webhookDelivered = true;
    }
  }

  const rawKey = record.hcad_num;
  const isBuildingOverride = rawKey.includes("#");
  const cleanHcadNum = isBuildingOverride ? rawKey.split("#")[0].trim() : rawKey;

  return {
    ok: true,
    webhookDelivered,
    overrideKey: rawKey,
    override: {
      id: rawKey,
      building_id: isBuildingOverride ? rawKey : "",
      hcad_num: cleanHcadNum,
      is_building_override: isBuildingOverride,
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

