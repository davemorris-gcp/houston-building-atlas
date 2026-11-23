import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

import {
  MONITORING_SCHEMA_CONTRACT,
  buildCampusParcelIndex,
  calendarDaysBetween,
  classifyProtectionLevel,
  compactHistoryJsonFifo,
  compactWatchlistDossierJson,
  computeAttentionLevel,
  computeContributingStatus,
  computeThreatStageFromEvents,
  deriveLegacyTierFromParcel,
  detectParcelSignalFlags,
  evaluateHistoricalBaselineEvent,
  explainAttentionLevel,
  mapAgencyOrPermitToThreatStage,
  mergeParcelAndDecisionConcurrent,
  migrateLegacyTierToParcelFields,
  mintSyntheticUnparceledId,
  normalizeHcad13,
  normalizeParcelRecord,
  resolveCanonicalParcelId,
  resolveEffectiveAreaContext,
  sanitizeMonitoringText,
  toChicagoCalendarDate,
} from "../../app/js/parcelMonitoringModel.js";

import {
  LS_ADMIN_SESSION_KEY,
  LS_AUTH_CACHE_PREFIX,
  LS_CROSS_TAB_BROADCAST_KEY,
  callMonitoringRpc,
  canAccessStaffOnly,
  decodeJwtPayloadUnsafe,
  ensureCleanUnauthenticatedStorageOnBoot,
  getActiveMonitoringRole,
  getPendingAuthRetryCount,
  isSessionTokenExpired,
  loadCachedMonitoringState,
  purgePrivateMonitoringStorage,
  replayPendingAuthRetries,
  saveCachedMonitoringState,
  signOutMonitoringSession,
  subscribeCrossTabAuthChanges,
  verifyAndSignInMonitoring,
} from "../../app/js/monitoringAuthClient.js";

import {
  buildApprovedOverrideWritebackPayload,
  detectParcelSourceConflicts,
  stripEmbeddedGeometryTagClient,
} from "../../app/js/sourceConflictResolver.js";

import {
  MODERATION_CATEGORIES,
  buildModerationDecisionPayload,
  classifyModerationQueueItem,
  filterModerationQueueItems,
} from "../../app/js/communityModerationQueue.js";

import {
  ADVOCACY_STATUS_LABELS,
  INTEGRITY_LABELS,
  OUTCOME_LABELS,
  SIGNIFICANCE_BASIS_LABELS,
  STORY_CHECK_STEPS,
  parseStoryCheckJson,
  renderModerationQueueDetailHtml,
  renderModerationQueueListHtml,
  renderParcelSignificanceCardHtml,
  renderSignedOutWatchlistGateHtml,
  renderSourceConflictWritebackCardHtml,
  renderStaffOnlyStrategyCardHtml,
  resolveEffectiveParcelAssessment,
  serializeStoryCheck,
} from "../../app/js/monitoringWorkbenchUi.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO_ROOT = path.resolve(__dirname, "../..");
const requireCjs = createRequire(import.meta.url);

function createMemoryStorage(initial = {}) {
  const map = new Map(Object.entries(initial).map(([k, v]) => [String(k), String(v)]));
  return {
    get length() {
      return map.size;
    },
    key(idx) {
      return Array.from(map.keys())[idx] ?? null;
    },
    keys() {
      return Array.from(map.keys());
    },
    getItem(k) {
      const key = String(k);
      return map.has(key) ? map.get(key) : null;
    },
    setItem(k, v) {
      map.set(String(k), String(v));
    },
    removeItem(k) {
      map.delete(String(k));
    },
    clear() {
      map.clear();
    },
  };
}

function makeSyntheticJwt(payloadObj) {
  const hdr = Buffer.from(JSON.stringify({ alg: "RS256", typ: "JWT" })).toString("base64url");
  const body = Buffer.from(JSON.stringify(payloadObj)).toString("base64url");
  return `${hdr}.${body}.sig_placeholder`;
}

function createPhase1AppsScriptHarness({
  staffPasskey = "staff-passkey-phase1",
  screenerPasskey = "screener-passkey-phase1",
  pipelineToken = "pipeline-secret-token-phase1",
  oauthClientId = "atlas-client-id.apps.googleusercontent.com",
  ownerEmail = "owner@preservation.example.org",
  editors = ["staff@preservation.example.org"],
  tokenDirectory = {},
  nowMs = 1795440000000,
} = {}) {
  const cjsPath = path.join(REPO_ROOT, "pipeline", "atlas_pipeline", "apps_script", "Code.cjs");
  const codeSrc = fs.readFileSync(cjsPath, "utf8");
  // Confirm single-quote invariant and zero typographic dashes on standalone Apps Script module
  assert.equal(codeSrc.includes('"'), false, "Code.cjs must contain zero literal double quotes");
  assert.equal(/[\u2013\u2014]/.test(codeSrc), false, "Code.cjs must contain zero en/em dashes");

  const exported = requireCjs(cjsPath);
  // Also verify Code.cjs evaluates cleanly inside an isolated Node vm context (matching Apps Script V8 globals)
  const vmSandbox = { console };
  vm.createContext(vmSandbox);
  vm.runInContext(codeSrc, vmSandbox);
  assert.equal(typeof vmSandbox.doGet, "function");
  assert.equal(typeof vmSandbox.doPost, "function");

  const workbooks = new Map();
  const cacheMap = new Map([["PLAT_SYNC_CSV_V1", "STALE_PRIVATE_PLAT_SYNC_ROW"]]);
  const propMap = new Map([
    ["GOOGLE_OAUTH_CLIENT_ID", oauthClientId],
    ["PIPELINE_SYNC_TOKEN", pipelineToken],
    ["STAFF_SYNC_PASSKEY", staffPasskey],
    ["SCREENER_SYNC_PASSKEY", screenerPasskey],
  ]);
  const driveFolders = new Map();
  let lockAcquisitions = 0;
  let currentNowMs = nowMs;

  function makeWorkbook(wbId, wbName) {
    const sheets = new Map();

    function createSheet(name, initialRows = [], hidden = false) {
      const grid = initialRows.map((r) => r.slice());
      const formulas = new Map();
      let isHidden = Boolean(hidden);

      function ensureBounds(rCount, cCount) {
        while (grid.length < rCount) grid.push([]);
        for (let r = 0; r < grid.length; r += 1) {
          while (grid[r].length < cCount) grid[r].push("");
        }
      }

      function parseA1Range(a1) {
        const s = String(a1).trim();
        const colIdx = (letters) => {
          let n = 0;
          for (const ch of letters.toUpperCase()) n = n * 26 + (ch.charCodeAt(0) - 64);
          return n;
        };
        const single = s.match(/^([A-Z]+)(\d+)$/i);
        if (single) {
          return { r1: Number(single[2]), c1: colIdx(single[1]), numRows: 1, numCols: 1 };
        }
        const fullCol = s.match(/^([A-Z]+):([A-Z]+)$/i);
        if (fullCol) {
          const c1 = colIdx(fullCol[1]);
          const c2 = colIdx(fullCol[2]);
          return {
            r1: 1,
            c1,
            numRows: Math.max(1, grid.length),
            numCols: c2 - c1 + 1,
          };
        }
        throw new Error(`Unsupported A1 range in mock sheet: ${a1}`);
      }

      function getRangeObj(r1, c1, numRows = 1, numCols = 1) {
        ensureBounds(r1 + numRows - 1, c1 + numCols - 1);
        return {
          getValue() {
            return grid[r1 - 1][c1 - 1];
          },
          getDisplayValue() {
            return String(grid[r1 - 1][c1 - 1] ?? "");
          },
          getFormula() {
            return formulas.get(`${r1}:${c1}`) || "";
          },
          setFormula(fStr) {
            formulas.set(`${r1}:${c1}`, String(fStr));
            grid[r1 - 1][c1 - 1] = String(fStr);
          },
          getValues() {
            const out = [];
            for (let r = 0; r < numRows; r += 1) {
              out.push(grid[r1 - 1 + r].slice(c1 - 1, c1 - 1 + numCols));
            }
            return out;
          },
          setValues(vals) {
            ensureBounds(r1 + vals.length - 1, c1 + (vals[0]?.length || 1) - 1);
            for (let r = 0; r < vals.length; r += 1) {
              for (let c = 0; c < vals[r].length; c += 1) {
                const rr = r1 + r;
                const cc = c1 + c;
                formulas.delete(`${rr}:${cc}`);
                grid[rr - 1][cc - 1] = vals[r][c];
              }
            }
          },
        };
      }

      const sheetApi = {
        getName: () => name,
        isSheetHidden: () => isHidden,
        hideSheet: () => {
          isHidden = true;
        },
        getLastRow: () => grid.length,
        deleteRow: (rowOneBased) => {
          if (rowOneBased >= 1 && rowOneBased <= grid.length) {
            grid.splice(rowOneBased - 1, 1);
          }
        },
        getDataRange: () => {
          const maxCols = grid.reduce((m, r) => Math.max(m, r.length), 1);
          return getRangeObj(1, 1, Math.max(1, grid.length), maxCols);
        },
        getRange: (...args) => {
          if (args.length === 1 && typeof args[0] === "string") {
            const p = parseA1Range(args[0]);
            return getRangeObj(p.r1, p.c1, p.numRows, p.numCols);
          }
          return getRangeObj(args[0], args[1], args[2] || 1, args[3] || 1);
        },
        _grid: grid,
        _formulas: formulas,
      };
      sheets.set(name, sheetApi);
      return sheetApi;
    }

    const wbApi = {
      getId: () => wbId,
      getName: () => wbName,
      getSheetByName: (n) => sheets.get(n) || null,
      insertSheet: (n) => createSheet(n, []),
      deleteSheet: (sh) => {
        if (sh && typeof sh.getName === "function") sheets.delete(sh.getName());
      },
      getSheets: () => Array.from(sheets.values()),
      getOwner: () => ({ getEmail: () => ownerEmail }),
      getEditors: () => editors.map((em) => ({ getEmail: () => em })),
      _createSheet: createSheet,
    };
    workbooks.set(wbId, wbApi);
    return wbApi;
  }

  const coreWb = makeWorkbook("core-wb-id", "Monitoring_Core");
  const cfgSheet = coreWb._createSheet("Instructions_&_Apps_Script", []);
  cfgSheet.getRange("B6").setValues([[staffPasskey]]);

  const pendingHeaders = [
    "status", "hcad_num", "address", "suggested_year_built", "original_hcad_year",
    "historic_district", "contributing", "source_type", "source_citation", "source_url",
    "reviewed_by", "submitted_by", "submitted_at", "moderation_notes", "building_name",
    "alt_names", "bld_style", "architect",
    "triage_bucket_formula", "map_link_formula", "hcad_link_formula", "row_num_formula",
    "geometry_geojson",
  ];
  const pendingSheet = coreWb._createSheet("Pending_Submissions", [pendingHeaders]);
  // Seed ARRAYFORMULAs in S1:V1 (cols 19..22) that must NEVER be overwritten!
  pendingSheet.getRange(1, 19).setFormula("=ARRAYFORMULA(S_TRIAGE_FORMULA)");
  pendingSheet.getRange(1, 20).setFormula("=ARRAYFORMULA(T_MAP_FORMULA)");
  pendingSheet.getRange(1, 21).setFormula("=ARRAYFORMULA(U_HCAD_FORMULA)");
  pendingSheet.getRange(1, 22).setFormula("=ARRAYFORMULA(V_ROW_FORMULA)");

  coreWb._createSheet("Approved_Edits", [
    ["status", "hcad_num", "address", "suggested_year_built"],
    ["Approved", "0621100000014", "1127 KEY ST", 1925],
  ]);

  const surveyHeaders = [
    "survey_id", "surveyed_at", "review_status", "hcad_num", "address",
    "district_or_corridor", "integrity_grade", "condition_rating", "suggested_contributing",
    "windows_status", "cladding_material", "porch_integrity", "atlas_year_built",
    "observed_year_or_demo_note", "photo_url", "field_notes", "surveyor_name", "gps_lat_lng_acc",
  ];
  coreWb._createSheet("Field_Surveys", [surveyHeaders]);

  const rolesSpec = exported.TABLE_REGISTRY._Monitoring_Roles;
  coreWb._createSheet(
    "_Monitoring_Roles",
    [
      rolesSpec.headers,
      ["staff@preservation.example.org", "staff", "all", "Preservation Staff", "true", "2026-11-23T00:00:00Z"],
      ["screener@preservation.example.org", "screener", "area:priority:sunnyside", "Screener", "true", "2026-11-23T00:00:00Z"],
      ["inactive@preservation.example.org", "staff", "all", "Preservation Staff", "false", "2026-11-23T00:00:00Z"],
    ],
    true
  );

  let createdWbSeq = 1;
  const driveFiles = [];
  driveFolders.set("drive-backup-folder-1", {
    createFile(fileName, content, mimeType) {
      const f = {
        fileName,
        content,
        mimeType,
        trashed: false,
        getName: () => fileName,
        getContent: () => content,
        isTrashed: () => f.trashed,
        setTrashed: (val) => {
          f.trashed = Boolean(val);
        },
      };
      driveFiles.push(f);
      return f;
    },
    getFiles() {
      const active = driveFiles.filter((f) => !f.trashed);
      let idx = 0;
      return {
        hasNext: () => idx < active.length,
        next: () => active[idx++],
      };
    },
  });

  const env = {
    nowMs: () => currentNowMs,
    spreadsheetApp: {
      getActiveSpreadsheet: () => coreWb,
      openById: (id) => {
        const wb = workbooks.get(id);
        if (!wb) throw new Error(`Workbook not found: ${id}`);
        return wb;
      },
      create: (title) => {
        const id = `created-wb-${createdWbSeq++}`;
        const wb = makeWorkbook(id, title);
        wb._createSheet("Sheet1", [[""]]);
        return wb;
      },
    },
    driveApp: {
      getFolderById: (id) => {
        const folder = driveFolders.get(id);
        if (!folder) throw new Error(`Drive folder not found: ${id}`);
        return folder;
      },
    },
    cacheService: {
      getScriptCache: () => ({
        get: (k) => (cacheMap.has(k) ? cacheMap.get(k) : null),
        put: (k, v) => cacheMap.set(k, String(v)),
        remove: (k) => cacheMap.delete(k),
      }),
    },
    propertiesService: {
      getScriptProperties: () => ({
        getProperty: (k) => (propMap.has(k) ? propMap.get(k) : null),
        setProperty: (k, v) => propMap.set(k, String(v)),
      }),
    },
    lockService: {
      getScriptLock: () => ({
        waitLock: () => {
          lockAcquisitions += 1;
        },
        releaseLock: () => {},
      }),
    },
    urlFetchApp: {
      fetch: (url) => {
        const u = new URL(url);
        const tok = u.searchParams.get("id_token") || "";
        const entry = tokenDirectory[tok];
        if (!entry) {
          return {
            getResponseCode: () => 400,
            getContentText: () => JSON.stringify({ error_description: "Invalid Value" }),
          };
        }
        return {
          getResponseCode: () => entry.status || 200,
          getContentText: () => JSON.stringify(entry.claims),
        };
      },
    },
    contentService: null,
  };

  const controller = exported.createAppsScriptController(env);

  return {
    TABLE_REGISTRY: exported.TABLE_REGISTRY,
    controller,
    coreWb,
    workbooks,
    cacheMap,
    propMap,
    driveFiles,
    getLockAcquisitions: () => lockAcquisitions,
    setNowMs: (ms) => {
      currentNowMs = ms;
    },
    callGet(params = {}) {
      const resp = controller.doGet({ parameter: params });
      return { mime: resp.getMimeType(), text: resp.getContent() };
    },
    callPost(bodyObj) {
      const resp = controller.doPost({
        postData: { contents: JSON.stringify(bodyObj) },
      });
      return JSON.parse(resp.getContent());
    },
  };
}

test("UT-1, UT-2, UT-3 & TST-1: monitoringSchemaContract parity and shared JSON vectors match 100% in JS", () => {
  const pyContractPath = path.join(
    REPO_ROOT,
    "pipeline",
    "atlas_pipeline",
    "contracts",
    "monitoring_schema_contract.json"
  );
  const pyContract = JSON.parse(fs.readFileSync(pyContractPath, "utf8"));
  assert.deepEqual(
    MONITORING_SCHEMA_CONTRACT,
    pyContract,
    "Browser monitoringSchemaContract.js and Python monitoring_schema_contract.json must be 100% identical"
  );

  const vectorsPath = path.join(REPO_ROOT, "tests", "fixtures", "phase1_monitoring_parity_vectors.json");
  const suite = JSON.parse(fs.readFileSync(vectorsPath, "utf8"));

  // 1. Attention & statutory protection vectors (including threat_stage === 6 and all 7 outcomes)
  for (const vec of suite.attentionVectors) {
    const actual = computeAttentionLevel(vec.parcel, {
      asOfDateCst: vec.asOfDateCst,
      nextHearingDateCst: vec.nextHearingDateCst,
      areasById: suite.areasById,
    });
    assert.equal(
      actual,
      vec.expectedAttention,
      `Attention vector ${vec.id} expected ${vec.expectedAttention}, got ${actual}`
    );
    const explanation = explainAttentionLevel(vec.parcel, {
      asOfDateCst: vec.asOfDateCst,
      nextHearingDateCst: vec.nextHearingDateCst,
      areasById: suite.areasById,
    });
    assert.ok(explanation.length > 5, `Explanation for ${vec.id} must be non-empty`);
    assert.equal(/[\u2013\u2014]/.test(explanation), false);
  }

  // 2. America/Chicago date normalization & calendar day deltas
  for (const dv of suite.chicagoDateVectors) {
    assert.equal(toChicagoCalendarDate(dv.input), dv.expectedChicagoYmd);
  }
  assert.equal(calendarDaysBetween("2026-11-23", "2026-12-07"), 14);
  assert.equal(calendarDaysBetween("2026-11-23", "2026-12-08"), 15);

  // 3. 180-day & pre-parcel.added_at historical baseline vectors
  for (const hv of suite.historicalBaselineVectors) {
    assert.equal(
      evaluateHistoricalBaselineEvent({
        filingDate: hv.filingDate,
        parcelAddedAt: hv.parcelAddedAt,
        asOfDateCst: hv.asOfDateCst,
      }),
      hv.expectedHistorical,
      `Historical baseline vector ${hv.id} mismatch`
    );
  }

  // 4. Multi-parcel CAMPUS:<slug> member HCAD + perimeter address rollup & signal flags
  const campusIndex = buildCampusParcelIndex(suite.campusAndSignalVectors.parcels);
  for (const lk of suite.campusAndSignalVectors.lookups) {
    assert.equal(
      resolveCanonicalParcelId(lk.query, campusIndex),
      lk.expectedParcelId,
      `Canonical parcel resolution mismatch for ${JSON.stringify(lk.query)}`
    );
  }
  for (const sc of suite.campusAndSignalVectors.signalCases) {
    assert.deepEqual(
      detectParcelSignalFlags({
        ownerOfRecord: sc.ownerOfRecord,
        partyNames: sc.partyNames,
        landUseClass: sc.landUseClass,
        eventKinds: sc.eventKinds,
      }),
      sc.expectedFlags,
      `Signal flags mismatch for ${sc.id}`
    );
  }

  // 5. Bidirectional legacy Tier 1..4 compatibility vectors
  for (const lv of suite.legacyCompatibilityVectors) {
    const migrated = migrateLegacyTierToParcelFields(lv.legacy, {
      isWatchlist: lv.isWatchlist,
      atlasKnownHigh: lv.atlasKnownHigh,
    });
    for (const [field, expectedVal] of Object.entries(lv.expectedMigrated)) {
      assert.equal(
        migrated[field],
        expectedVal,
        `Legacy migration vector ${lv.id} field ${field} mismatch`
      );
    }
    assert.equal(
      deriveLegacyTierFromParcel(migrated),
      lv.expectedDerivedTier,
      `Derived legacy tier mismatch for ${lv.id}`
    );
  }

  // 6. Agency -> Threat Stage & historical baseline threat filtering
  assert.equal(mapAgencyOrPermitToThreatStage({ sourceAgency: "chk-01", eventKind: "Broker Listing" }), 1);
  assert.equal(mapAgencyOrPermitToThreatStage({ sourceAgency: "chk-05", eventKind: "Deed Transfer" }), 2);
  assert.equal(mapAgencyOrPermitToThreatStage({ sourceAgency: "chk-09", eventKind: "C3N Replat" }), 3);
  assert.equal(mapAgencyOrPermitToThreatStage({ sourceAgency: "chk-13", eventKind: "Dangerous Building" }), 4);
  assert.equal(mapAgencyOrPermitToThreatStage({ sourceAgency: "chk-14", eventKind: "Sewer Disconnect" }), 4);
  assert.equal(mapAgencyOrPermitToThreatStage({ sourceAgency: "chk-14", eventKind: "Building Demolition", summaryText: "Type SD Building Demolition" }), 5);
  assert.equal(
    computeThreatStageFromEvents(
      [
        { threat_stage: 5, is_historical_baseline: true },
        { threat_stage: 2, is_historical_baseline: false },
      ],
      { advocacy_status: "monitoring", outcome: "" }
    ),
    2,
    "Historical baseline events (>180d) must not escalate active threat_stage"
  );
});

test("UT-6: 48,000-char FIFO compaction on history_json and Watchlist_Dossiers preserves valid JSON and strips dashes", () => {
  const hugeEntries = Array.from({ length: 180 }, (_, idx) => ({
    at: `2026-11-${String((idx % 28) + 1).padStart(2, "0")}T12:00:00Z`,
    role: "staff",
    by: "Preservation Staff",
    note: `Entry #${idx} ${String.fromCharCode(0x2014)} long archival note ${"X".repeat(420)}`,
  }));
  const compactedHistory = compactHistoryJsonFifo(
    hugeEntries.map((e) => ({ ...e, note: sanitizeMonitoringText(e.note) })),
    48000
  );
  assert.ok(compactedHistory.length <= 48000);
  assert.equal(/[\u2013\u2014]/.test(compactedHistory), false);
  const parsedHist = JSON.parse(compactedHistory);
  assert.ok(parsedHist.truncatedCount > 0, "FIFO compaction must record truncatedCount");
  assert.equal(
    parsedHist.entries.at(-1).note.startsWith("Entry #179 - long archival note"),
    true,
    "Newest history entries must be retained when oldest entries are dropped FIFO"
  );

  const hugeDossier = {
    id: "prop-huge-dossier",
    propertyName: "Historic Warehouse Complex",
    address: "1200 Commerce St",
    photoDataUrl: `data:image/jpeg;base64,${"A".repeat(35000)}`,
    findingsLog: Array.from({ length: 60 }, (_, i) => `Finding ${i}: ${"B".repeat(300)}`),
    checklist: Array.from({ length: 15 }, (_, cIdx) => ({
      id: `chk-${String(cIdx + 1).padStart(2, "0")}`,
      status: "Flagged",
      checks: Array.from({ length: 20 }, (__, kIdx) => ({
        date: "2026-11-10",
        result: `Check ${cIdx}-${kIdx}: ${"C".repeat(260)}`,
      })),
    })),
    notes: "N".repeat(12000),
  };
  const compactedDossier = compactWatchlistDossierJson(hugeDossier, 48000);
  assert.ok(compactedDossier.length <= 48000, `Expected <= 48000 chars, got ${compactedDossier.length}`);
  const parsedDossier = JSON.parse(compactedDossier);
  assert.equal(parsedDossier.id, "prop-huge-dossier");
  assert.equal(parsedDossier.photoDataUrl, "");
  assert.equal(parsedDossier.photoDataUrlOmittedInCompactCell, true);
  assert.ok(parsedDossier.truncatedCheckCount > 0);
});

test("IT-1 & MOD-1: Apps Script doGet/doPost enforces OIDC aud/exp/email_verified, blocks passkey spoofing, isolates Screener vs Staff vs Pipeline, and supports header-reordered tables", () => {
  const validStaffJwt = "jwt-valid-staff";
  const validScreenerJwt = "jwt-valid-screener";
  const wrongAudJwt = "jwt-wrong-aud";
  const unverifiedEmailJwt = "jwt-unverified";
  const expiredJwt = "jwt-expired";
  const inactiveJwt = "jwt-inactive";

  const nowSec = Math.floor(1795440000000 / 1000);
  const h = createPhase1AppsScriptHarness({
    tokenDirectory: {
      [validStaffJwt]: {
        claims: {
          aud: "atlas-client-id.apps.googleusercontent.com",
          email: "staff@preservation.example.org",
          email_verified: "true",
          exp: String(nowSec + 3600),
        },
      },
      [validScreenerJwt]: {
        claims: {
          aud: "atlas-client-id.apps.googleusercontent.com",
          email: "screener@preservation.example.org",
          email_verified: true,
          exp: String(nowSec + 3600),
        },
      },
      [wrongAudJwt]: {
        claims: {
          aud: "foreign-third-party-app.apps.googleusercontent.com",
          email: "staff@preservation.example.org",
          email_verified: true,
          exp: String(nowSec + 3600),
        },
      },
      [unverifiedEmailJwt]: {
        claims: {
          aud: "atlas-client-id.apps.googleusercontent.com",
          email: "staff@preservation.example.org",
          email_verified: false,
          exp: String(nowSec + 3600),
        },
      },
      [expiredJwt]: {
        claims: {
          aud: "atlas-client-id.apps.googleusercontent.com",
          email: "staff@preservation.example.org",
          email_verified: true,
          exp: String(nowSec - 120),
        },
      },
      [inactiveJwt]: {
        claims: {
          aud: "atlas-client-id.apps.googleusercontent.com",
          email: "inactive@preservation.example.org",
          email_verified: true,
          exp: String(nowSec + 3600),
        },
      },
    },
  });

  // 1. doGet(?feed=plat_sync) is retired and evicts stale PLAT_SYNC_CSV_V1 cache
  assert.equal(h.cacheMap.has("PLAT_SYNC_CSV_V1"), true);
  const retiredSync = h.callGet({ feed: "plat_sync" });
  assert.equal(h.cacheMap.has("PLAT_SYNC_CSV_V1"), false, "PLAT_SYNC_CSV_V1 must be evicted on doGet");
  const retiredJson = JSON.parse(retiredSync.text);
  assert.equal(retiredJson.ok, false);
  assert.equal(retiredJson.authorized, false);

  // Default public doGet still serves Approved_Edits CSV cleanly
  const pubCsv = h.callGet({});
  assert.match(pubCsv.text, /0621100000014/);

  // 2. Negative OIDC & passkey spoofing matrix
  for (const badTok of [wrongAudJwt, unverifiedEmailJwt, expiredJwt, inactiveJwt, "unknown-jwt"]) {
    const res = h.callPost({ action: "verify_admin", google_id_token: badTok });
    assert.equal(res.authorized, false, `Token ${badTok} must be rejected`);
  }
  // Blank email + valid Staff passkey must NEVER default to Workbook Owner
  assert.equal(
    h.callPost({ action: "verify_admin", admin_email: "", admin_passkey: "staff-passkey-phase1" }).authorized,
    false
  );
  // Screener email + Staff passkey must NOT authenticate or escalate
  assert.equal(
    h.callPost({
      action: "verify_admin",
      admin_email: "screener@preservation.example.org",
      admin_passkey: "staff-passkey-phase1",
    }).authorized,
    false
  );
  // Staff email + Screener passkey must NOT authenticate as Staff
  assert.equal(
    h.callPost({
      action: "verify_admin",
      admin_email: "staff@preservation.example.org",
      admin_passkey: "screener-passkey-phase1",
    }).authorized,
    false
  );

  // 3. Provision vaults as verified Staff and reorder columns on Parcels & Staff_Parcel_Strategy to prove MOD-1 header-keyed independence
  const bootRes = h.callPost({
    action: "bootstrap_phase1_vaults",
    google_id_token: validStaffJwt,
  });
  assert.equal(bootRes.ok, true);
  assert.ok(bootRes.staffOnlySheetId);
  assert.ok(bootRes.backupVaultSheetId);

  // Reorder Parcels sheet columns intentionally (move primary_address and significance_level earlier)
  const parcelsSheet = h.coreWb.getSheetByName("Parcels");
  const origParcelHeaders = h.TABLE_REGISTRY.Parcels.headers.slice();
  const reorderedHeaders = [
    "significance_level",
    "primary_address",
    ...origParcelHeaders.filter((col) => col !== "significance_level" && col !== "primary_address"),
  ];
  parcelsSheet.getRange(1, 1, 1, reorderedHeaders.length).setValues([reorderedHeaders]);

  // Seed confidential Staff_Parcel_Strategy & Watched_People_And_Firms rows
  const staffSave = h.callPost({
    action: "upsert_staff_only",
    google_id_token: validStaffJwt,
    parcelStrategy: [
      {
        parcel_id: "0152300000001",
        hcad_num: "0152300000001",
        strategy_notes: "CONFIDENTIAL_BOARD_EASEMENT_STRATEGY",
        contact_notes: "CONFIDENTIAL_DONOR_CONTACT",
        owner_relationship_notes: "CONFIDENTIAL_COUNSEL_CALL",
      },
    ],
    watchedEntities: [
      {
        entity_id: "ENT:BAYOU_DEMO_LLC",
        entity_name: "Bayou Demo Holdings LLC",
        normalized_key: "BAYOU DEMO",
        entity_role: "developer_llc",
        pattern_notes: "CONFIDENTIAL_REPEAT_TEARDOWN_NOTES",
        active: true,
      },
    ],
  });
  assert.equal(staffSave.ok, true);

  // 4. Screener RBAC isolation: receives Core tables, ZERO staffOnly, ZERO pendingModerationCount, and cannot mutate Staff fields or vaults
  const screenerVerify = h.callPost({ action: "verify_admin", google_id_token: validScreenerJwt });
  assert.equal(screenerVerify.authorized, true);
  assert.equal(screenerVerify.role, "screener");

  const screenerPull = h.callPost({ action: "pull_monitoring_state", google_id_token: validScreenerJwt });
  assert.equal(screenerPull.ok, true);
  assert.equal(screenerPull.role, "screener");
  assert.equal("staffOnly" in screenerPull, false, "Screener payload must never include staffOnly");
  assert.equal("pendingModerationCount" in screenerPull, false);
  assert.equal(JSON.stringify(screenerPull).includes("CONFIDENTIAL_"), false);

  // Screener forbidden endpoints & forbidden decision columns
  assert.equal(
    h.callPost({
      action: "upsert_staff_only",
      google_id_token: validScreenerJwt,
      parcelStrategy: [{ parcel_id: "0152300000001", strategy_notes: "hack" }],
    }).authorized,
    false
  );
  assert.equal(
    h.callPost({ action: "list_pending_submissions", google_id_token: validScreenerJwt }).authorized,
    false
  );
  assert.equal(
    h.callPost({
      action: "moderate_submission",
      google_id_token: validScreenerJwt,
      rowNumber: 2,
      decision: "Approved",
    }).authorized,
    false
  );
  assert.equal(
    h.callPost({
      action: "admin_approve",
      google_id_token: validScreenerJwt,
      hcad_num: "0621100000014",
      suggested_year_built: 1924,
    }).authorized,
    false
  );
  for (const forbiddenCol of [
    "significance_level",
    "contributing_override",
    "advocacy_status",
    "outcome",
    "attention_override",
  ]) {
    const attempt = h.callPost({
      action: "upsert_parcel_decision",
      google_id_token: validScreenerJwt,
      parcel_id: "0152300000001",
      patch: { [forbiddenCol]: "high" },
    });
    assert.equal(attempt.ok, false);
    assert.equal(attempt.forbiddenField, forbiddenCol);
  }
  // Screener cannot promote a brand-new Watchlist dossier
  assert.equal(
    h.callPost({
      action: "upsert_watchlist_dossier",
      google_id_token: validScreenerJwt,
      dossier: { id: "prop-new-unauthorized", address: "100 Main St" },
    }).authorized,
    false
  );

  // 5. Pipeline token isolation: can pull/push/backup, receives entity names ONLY without pattern_notes/strategy_notes, and cannot mutate Staff_Only or moderate
  const pipePull = h.callPost({
    action: "pipeline_pull_monitoring",
    pipeline_token: "pipeline-secret-token-phase1",
  });
  assert.equal(pipePull.ok, true);
  assert.equal("staffOnly" in pipePull, false);
  assert.equal(pipePull.watchedEntitiesForMatching.length, 1);
  assert.equal(pipePull.watchedEntitiesForMatching[0].entity_name, "Bayou Demo Holdings LLC");
  assert.equal("pattern_notes" in pipePull.watchedEntitiesForMatching[0], false);
  assert.equal(JSON.stringify(pipePull).includes("CONFIDENTIAL_"), false);

  for (const disallowedAction of ["upsert_staff_only", "moderate_submission", "admin_approve", "verify_admin"]) {
    const denied = h.callPost({
      action: disallowedAction,
      pipeline_token: "pipeline-secret-token-phase1",
    });
    assert.equal(denied.authorized, false, `pipeline_token must be denied for ${disallowedAction}`);
  }

  // 6. Staff receives full role-scoped state including Staff_Only strategy & entities
  const staffPull = h.callPost({ action: "pull_monitoring_state", google_id_token: validStaffJwt });
  assert.equal(staffPull.ok, true);
  assert.equal(staffPull.role, "staff");
  assert.equal(staffPull.staffOnly.parcelStrategy[0].strategy_notes, "CONFIDENTIAL_BOARD_EASEMENT_STRATEGY");
  assert.equal(staffPull.staffOnly.watchedEntities[0].entity_role, "developer_llc");
});

test("IT-2: Concurrent T0/T1/T2 pipeline-vs-human merges, Screener-after-Staff preservation, expected_updated_at conflict guard, and 60-snapshot Drive pruning", () => {
  const staffJwt = "jwt-staff";
  const screenerJwt = "jwt-screener";
  const nowSec = Math.floor(1795440000000 / 1000);
  const h = createPhase1AppsScriptHarness({
    tokenDirectory: {
      [staffJwt]: {
        claims: {
          aud: "atlas-client-id.apps.googleusercontent.com",
          email: "staff@preservation.example.org",
          email_verified: true,
          exp: String(nowSec + 3600),
        },
      },
      [screenerJwt]: {
        claims: {
          aud: "atlas-client-id.apps.googleusercontent.com",
          email: "screener@preservation.example.org",
          email_verified: true,
          exp: String(nowSec + 3600),
        },
      },
    },
  });
  h.callPost({ action: "bootstrap_phase1_vaults", google_id_token: staffJwt });

  // Seed initial parcel + Watchlist dossier at T0
  h.callPost({
    action: "pipeline_push_sweep_results",
    pipeline_token: "pipeline-secret-token-phase1",
    areas: [
      {
        area_id: "area:priority:third_ward",
        name: "Third Ward",
        area_type: "neighborhood",
        priority_status: "priority",
        age_rule_cutoff_year: 1976,
      },
    ],
    parcels: [
      {
        parcel_id: "0420090000001",
        hcad_num: "0420090000001",
        primary_address: "2802 Wheeler Ave",
        year_built: 1928,
        area_ids: "area:priority:third_ward",
        review_status: "not_reviewed",
        threat_stage: 2,
      },
    ],
    watchlistDossiers: [
      {
        id: "prop-2802-wheeler",
        parcelId: "0420090000001",
        hcadAccount: "0420090000001",
        address: "2802 Wheeler Ave",
        checklist: [
          {
            id: "chk-02",
            status: "Complete",
            humanStatusEdited: true,
            checks: [{ date: "2026-11-20", source: "manual", result: "Staff field photo logged" }],
          },
          {
            id: "chk-14",
            status: "To Do",
            checks: [],
          },
        ],
      },
    ],
  });

  // T0: Pipeline pulls stale not_reviewed snapshot before running external agency queries
  const t0Pull = h.callPost({
    action: "pipeline_pull_monitoring",
    pipeline_token: "pipeline-secret-token-phase1",
  });
  assert.equal(t0Pull.parcels[0].review_status, "not_reviewed");

  // T1a: Staff confirms high significance + active_advocacy while pipeline sweep is in flight
  const t1Staff = h.callPost({
    action: "upsert_parcel_decision",
    google_id_token: staffJwt,
    parcel_id: "0420090000001",
    patch: {
      review_status: "assessed",
      significance_level: "high",
      significance_basis: "community_cultural",
      integrity: "good",
      advocacy_status: "active_advocacy",
      attention_override: "watch",
      screening_notes: "Confirmed 1928 Washington Terrace brick duplex.",
    },
  });
  assert.equal(t1Staff.ok, true);
  assert.equal(t1Staff.parcel.significance_level, "high");
  assert.equal(t1Staff.parcel.attention_level, "watch");

  // T1b: Screener adds 10-Minute Story Check note after Staff confirmation -> must preserve Staff significance_level & attention_override!
  const t1Screener = h.callPost({
    action: "upsert_parcel_decision",
    google_id_token: screenerJwt,
    parcel_id: "0420090000001",
    expected_updated_at: t1Staff.decision.decided_at,
    patch: {
      recommended_significance: "moderate",
      screening_notes: "Confirmed 1928 Washington Terrace brick duplex. Added Sanborn sheet reference.",
      story_check_json: { year_source: true, district_context: true, note: "Verified on 1950 Sanborn" },
    },
  });
  assert.equal(t1Screener.ok, true);
  assert.equal(t1Screener.parcel.significance_level, "high", "Screener save must preserve Staff significance_level");
  assert.equal(t1Screener.parcel.attention_override, "watch", "Screener save must preserve Staff attention_override");

  // Stale expected_updated_at must surface deterministic conflict without data loss
  const staleAttempt = h.callPost({
    action: "upsert_parcel_decision",
    google_id_token: screenerJwt,
    parcel_id: "0420090000001",
    expected_updated_at: "2020-01-01T00:00:00.000Z",
    patch: { screening_notes: "Stale tab overwrite attempt" },
  });
  assert.equal(staleAttempt.ok, false);
  assert.equal(staleAttempt.conflict, true);
  assert.match(staleAttempt.currentDecision.screening_notes, /Added Sanborn sheet reference/);

  // Clear Staff attention_override so T2 sweep recalculates natural attention_level ("act") from stored human significance_level ("high") + new threat_stage (5)
  h.callPost({
    action: "upsert_parcel_decision",
    google_id_token: staffJwt,
    parcel_id: "0420090000001",
    patch: { attention_override: "" },
  });

  // T2: Pipeline pushes sweep results constructed from stale T0 snapshot (review_status: "not_reviewed", significance_level: "") + new Stage 5 demolition alert!
  const t2Push = h.callPost({
    action: "pipeline_push_sweep_results",
    pipeline_token: "pipeline-secret-token-phase1",
    parcels: [
      {
        parcel_id: "0420090000001",
        review_status: "not_reviewed",
        significance_level: "",
        threat_stage: 5,
        signal_flags: "owner_applicant_mismatch",
        owner_of_record: "WHEELER REDEVELOPMENT LP",
      },
    ],
    events: [
      {
        event_id: "EV:COH:0420090000001:26099111",
        parcel_id: "0420090000001",
        hcad_num: "0420090000001",
        threat_stage: 5,
        source_agency: "chk-14",
        event_kind: "Demolition Permit",
        filing_date: "2026-11-23",
        detected_at: "2026-11-23T12:05:00Z",
        is_historical_baseline: false,
        headline: "BLD DEMO permit #26099111",
      },
    ],
    watchlistDossiers: [
      {
        id: "prop-2802-wheeler",
        checklist: [
          {
            id: "chk-02",
            status: "To Do",
            checks: [],
          },
          {
            id: "chk-14",
            status: "Flagged",
            lastAutoSweepAt: "2026-11-23T12:05:00Z",
            checks: [
              {
                date: "2026-11-23",
                source: "[AUTO-SWEEP]",
                result: "[AUTO-SWEEP] COH Demo Permit #26099111",
              },
            ],
          },
        ],
      },
    ],
  });
  assert.equal(t2Push.ok, true);

  const afterT2 = h.callPost({ action: "pull_monitoring_state", google_id_token: staffJwt });
  const mergedParcel = afterT2.parcels.find((p) => p.parcel_id === "0420090000001");
  assert.equal(mergedParcel.review_status, "assessed", "T2 sweep must NOT clobber T1 human review_status");
  assert.equal(mergedParcel.significance_level, "high", "T2 sweep must NOT clobber T1 Staff significance_level");
  assert.equal(Number(mergedParcel.threat_stage), 5, "T2 sweep must advance machine-owned threat_stage");
  assert.equal(mergedParcel.attention_level, "act", "T2 sweep must recompute attention_level = act");
  assert.equal(afterT2.events.length, 1);

  const mergedWl = afterT2.watchlistDossiers.find((d) => d.id === "prop-2802-wheeler");
  const chk02 = mergedWl.checklist.find((c) => c.id === "chk-02");
  const chk14 = mergedWl.checklist.find((c) => c.id === "chk-14");
  assert.equal(chk02.status, "Complete", "Human-edited checklist row status must survive T2 sweep merge");
  assert.equal(chk02.checks.length, 1, "Human manual check note must survive T2 sweep merge");
  assert.equal(chk14.status, "Flagged");
  assert.equal(chk14.checks[0].result, "[AUTO-SWEEP] COH Demo Permit #26099111");

  // Verify pure JS mergeParcelAndDecisionConcurrent parity as well
  const jsMerge = mergeParcelAndDecisionConcurrent({
    existingParcel: mergedParcel,
    existingDecision: afterT2.decisions[0],
    patch: { significance_level: "not_significant" },
    actorRole: "screener",
    actorLabel: "Screener",
  });
  assert.equal(jsMerge.ok, false);
  assert.equal(jsMerge.forbiddenField, "significance_level");

  // Decoupled Drive backup runs WITHOUT acquiring the sheet mutation lock and prunes snapshots > 60
  h.propMap.set("BACKUP_FOLDER_ID", "drive-backup-folder-1");
  for (let i = 0; i < 62; i += 1) {
    h.driveFiles.push({
      fileName: `atlas_monitoring_backup_2026-09-${String((i % 30) + 1).padStart(2, "0")}-${String(i).padStart(2, "0")}.json`,
      content: "{}",
      trashed: false,
      getName() {
        return this.fileName;
      },
      setTrashed(v) {
        this.trashed = Boolean(v);
      },
    });
  }
  const locksBeforeBackup = h.getLockAcquisitions();
  const backupRes = h.callPost({
    action: "pipeline_create_drive_backup",
    pipeline_token: "pipeline-secret-token-phase1",
  });
  assert.equal(backupRes.ok, true);
  assert.equal(backupRes.target, "drive_folder");
  assert.equal(backupRes.prunedCount, 3, "63 total backup files -> 3 oldest trashed to retain 60");
  assert.equal(
    h.getLockAcquisitions(),
    locksBeforeBackup,
    "Decoupled pipeline_create_drive_backup must execute outside the sheet mutation lock"
  );
});

test("IT-6, DF-8 & RP-6: Moderation Queue & 1-Click Approved_Edits Writeback preserve Pending_Submissions!S:V ARRAYFORMULAs, resolve _FLAG rows without blank years, promote Field_Surveys, and strip ghost polygons on Lost/Vacant parcels", () => {
  const staffJwt = "jwt-staff";
  const nowSec = Math.floor(1795440000000 / 1000);
  const h = createPhase1AppsScriptHarness({
    tokenDirectory: {
      [staffJwt]: {
        claims: {
          aud: "atlas-client-id.apps.googleusercontent.com",
          email: "staff@preservation.example.org",
          email_verified: true,
          exp: String(nowSec + 3600),
        },
      },
    },
  });

  // 1. Public submissions: (a) archival year edit with custom polygon, (b) PHOTO_MISATTRIBUTION_FLAG, and (c) Walk-the-Block Field_Survey
  const customPolyGeoJson = JSON.stringify({
    type: "Polygon",
    coordinates: [[[-95.36, 29.75], [-95.359, 29.75], [-95.359, 29.751], [-95.36, 29.751], [-95.36, 29.75]]],
  });
  assert.equal(
    h.callPost({
      hcad_num: "0420090000001",
      address: "2802 WHEELER AVE",
      suggested_year_built: 1928,
      hcad_year_built: 1940,
      historic_district: "Washington Terrace",
      source_type: "Houston City Directory",
      source_citation: `1928 Polk Directory [GEOMETRY_GEOJSON: ${customPolyGeoJson}]`,
      geometry_geojson: customPolyGeoJson,
      submitter_name: "Community Researcher",
    }).status,
    "Pending"
  );

  assert.equal(
    h.callPost({
      hcad_num: "0610870390001",
      address: "1502 HEIGHTS BLVD",
      source_type: "PHOTO_MISATTRIBUTION_FLAG",
      source_citation: "Photo shows neighboring house across street",
      submitter_name: "Neighborhood Volunteer",
    }).status,
    "Pending"
  );

  const surveyPost = h.callPost({
    action: "field_survey",
    survey_id: "SURV_TEST_001",
    hcad_num: "0770010000001",
    address: "4110 REED RD",
    district_or_corridor: "Sunnyside",
    integrity_grade: "Intact",
    condition_rating: "Good",
    suggested_contributing: "Contributing",
    atlas_year_built: 1950,
    observed_year_or_demo_note: "1948",
    field_notes: "Intact Minimal Traditional bungalow",
    surveyor_name: "Block Walker",
  });
  assert.equal(surveyPost.ok, true);
  assert.equal(surveyPost.routed_to, "Field_Surveys");

  // 2. Unified list_pending_submissions returns all 3 items across Pending_Submissions & Field_Surveys
  const queueResp = h.callPost({
    action: "list_pending_submissions",
    google_id_token: staffJwt,
  });
  assert.equal(queueResp.ok, true);
  assert.equal(queueResp.items.length, 3);
  assert.equal(MODERATION_CATEGORIES.length, 5);
  assert.equal(filterModerationQueueItems(queueResp.items, { category: "field_survey" }).length, 1);
  assert.equal(filterModerationQueueItems(queueResp.items, { category: "dispute_flag" }).length, 1);

  // 3. Moderate archival submission -> Approved
  const archItem = classifyModerationQueueItem(queueResp.items[0]);
  const approveArchPayload = buildModerationDecisionPayload(archItem, "Approved");
  const modArchRes = h.callPost({ ...approveArchPayload, google_id_token: staffJwt });
  assert.equal(modArchRes.ok, true);
  assert.equal(modArchRes.status, "Approved");

  // 4. Moderate PHOTO_MISATTRIBUTION_FLAG with "Approved" -> automatically resolves to "Resolved" with blank year!
  const flagItem = classifyModerationQueueItem(queueResp.items[1]);
  assert.equal(flagItem.isDisputeFlag, true);
  const flagPayload = buildModerationDecisionPayload(flagItem, "Approved");
  assert.equal(flagPayload.decision, "Resolved");
  const modFlagRes = h.callPost({ ...flagPayload, google_id_token: staffJwt });
  assert.equal(modFlagRes.ok, true);
  assert.equal(modFlagRes.status, "Resolved");

  // 5. Promote Field_Surveys row -> marks Field_Surveys Col C "Promoted to Override" & appends Approved row in Pending_Submissions
  const surveyItem = classifyModerationQueueItem(queueResp.items[2]);
  assert.equal(surveyItem.isFieldSurvey, true);
  const promoteSurveyPayload = buildModerationDecisionPayload(surveyItem, "Approved");
  const modSurveyRes = h.callPost({ ...promoteSurveyPayload, google_id_token: staffJwt });
  assert.equal(modSurveyRes.ok, true);
  assert.equal(modSurveyRes.status, "Promoted to Override");

  // Row-shift protection on Pending_Submissions
  const shiftConflict = h.callPost({
    action: "moderate_submission",
    google_id_token: staffJwt,
    sourceTab: "Pending_Submissions",
    rowNumber: 2,
    expected_hcad_num: "9999999999999",
    decision: "Rejected",
  });
  assert.equal(shiftConflict.ok, false);
  assert.equal(shiftConflict.conflict, true);

  // 6. Source Conflict Resolver + 1-click Lost / Vacant Lot writeback strips stale custom polygon!
  const conflicts = detectParcelSourceConflicts({
    hcadNum: "0420090000001",
    platItem: { prevSub: "Riverside Terrace", superN: "Greater Third Ward", atlas: { earliestYearBuilt: 1940 } },
    parcelRecord: {
      hcad_num: "0420090000001",
      year_built: 1928,
      hcad_year_built: 1940,
      subdivision_name: "Washington Terrace",
      outcome: "lost",
    },
    curatedOverride: {
      hcad_num: "0420090000001",
      year_built: 1928,
      historic_district: "Washington Terrace",
      geometry: JSON.parse(customPolyGeoJson),
      source_citation: `1928 Polk Directory [GEOMETRY_GEOJSON: ${customPolyGeoJson}]`,
    },
  });
  assert.equal(conflicts.hasConflicts, true);
  assert.equal(conflicts.isDemolishedWriteback, true);
  assert.ok(conflicts.fields.some((f) => f.fieldKey === "year_built"));
  assert.ok(conflicts.fields.some((f) => f.fieldKey === "neighborhood_or_subdivision"));
  assert.ok(conflicts.fields.some((f) => f.fieldKey === "standing_status"));

  const writebackPayload = buildApprovedOverrideWritebackPayload({
    hcadNum: "0420090000001",
    primaryAddress: "2802 Wheeler Ave",
    isDemolishedVacant: true,
    existingOverride: {
      year_built: 1928,
      original_hcad_year: 1940,
      source_citation: `1928 Polk Directory [GEOMETRY_GEOJSON: ${customPolyGeoJson}]`,
    },
  });
  assert.equal(writebackPayload.suppress_only, true);
  assert.equal(writebackPayload.suggested_year_built, 0);
  assert.equal(writebackPayload.geometry_geojson, "");
  assert.equal(writebackPayload.source_citation.includes("[GEOMETRY_GEOJSON:"), false);
  assert.equal(stripEmbeddedGeometryTagClient(`Note [GEOMETRY_GEOJSON: ${customPolyGeoJson}] end`), "Note end");

  const adminWriteRes = h.callPost({
    action: "admin_approve",
    google_id_token: staffJwt,
    ...writebackPayload,
  });
  assert.equal(adminWriteRes.ok, true);
  assert.equal(adminWriteRes.upserted, true, "1-click writeback must upsert existing Approved row in place");
  assert.equal(adminWriteRes.rowNumber, 2);

  // Verify Pending_Submissions!S1:V1 ARRAYFORMULAs survived 100% intact and row 2 Col W polygon was stripped!
  const pendSheet = h.coreWb.getSheetByName("Pending_Submissions");
  assert.equal(pendSheet.getRange(1, 19).getFormula(), "=ARRAYFORMULA(S_TRIAGE_FORMULA)");
  assert.equal(pendSheet.getRange(1, 20).getFormula(), "=ARRAYFORMULA(T_MAP_FORMULA)");
  assert.equal(pendSheet.getRange(1, 21).getFormula(), "=ARRAYFORMULA(U_HCAD_FORMULA)");
  assert.equal(pendSheet.getRange(1, 22).getFormula(), "=ARRAYFORMULA(V_ROW_FORMULA)");
  assert.equal(pendSheet.getRange(2, 4).getValue(), 0, "Vacant suppression must set suggested_year_built = 0");
  assert.equal(pendSheet.getRange(2, 23).getValue(), "", "Vacant suppression must clear stale Col W geometry_geojson");
  assert.equal(
    String(pendSheet.getRange(2, 9).getValue()).includes("[GEOMETRY_GEOJSON:"),
    false,
    "Vacant suppression must strip embedded [GEOMETRY_GEOJSON:...] tag from citation"
  );
  assert.equal(pendSheet.getRange(3, 1).getValue(), "Resolved");
  assert.equal(pendSheet.getRange(3, 4).getValue(), "", "Resolved flag row must keep blank year");
});

test("MOD-3 & DF-10: monitoringAuthClient JWT expiry retry queue, Staff->Screener cache scrub, cross-tab Sign Out broadcast, and UI enum round-trip", async () => {
  const localStore = createMemoryStorage({
    "phpam:watchlist:v2": '{"secret":"legacy_dossier"}',
    "phpam:history": '["legacy"]',
    "phpam:cycle:2026-10-29": '{"secret":"legacy_plat"}',
  });
  const sessionStore = createMemoryStorage();

  // 1. Unauthenticated boot scrubs legacy un-namespaced private keys immediately
  assert.equal(ensureCleanUnauthenticatedStorageOnBoot({ localStore, sessionStore }), null);
  assert.equal(localStore.getItem("phpam:watchlist:v2"), null);
  assert.equal(localStore.getItem("phpam:cycle:2026-10-29"), null);

  // 2. Staff sign-in + Staff cache persistence
  const nowMs = Date.now();
  const validStaffJwt = makeSyntheticJwt({
    email: "staff@preservation.example.org",
    exp: Math.floor(nowMs / 1000) + 3600,
  });
  const signInStaff = await verifyAndSignInMonitoring({
    webhookUrl: "https://example.org/exec",
    googleIdToken: validStaffJwt,
    localStore,
    sessionStore,
    fetchImpl: async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        ok: true,
        authorized: true,
        email: "staff@preservation.example.org",
        role: "staff",
        displayLabel: "Preservation Staff",
        supportsRoamingSync: true,
        supportsPhase1Monitoring: true,
      }),
    }),
  });
  assert.equal(signInStaff.ok, true);
  assert.equal(getActiveMonitoringRole({ localStore, sessionStore }), "staff");
  assert.equal(canAccessStaffOnly(signInStaff.session), true);

  saveCachedMonitoringState(
    "staff@preservation.example.org",
    "staff",
    {
      parcels: [{ parcel_id: "0152300000001" }],
      staffOnly: { parcelStrategy: [{ parcel_id: "0152300000001", strategy_notes: "PRIVATE_STAFF" }], watchedEntities: [] },
      pendingModerationCount: 4,
    },
    { localStore }
  );
  assert.ok(localStore.getItem(`${LS_AUTH_CACHE_PREFIX}staff@preservation.example.org`));

  // 3. Shared-browser Staff -> Screener switch purges prior Staff cache immediately
  const validScreenerJwt = makeSyntheticJwt({
    email: "screener@preservation.example.org",
    exp: Math.floor(nowMs / 1000) + 3600,
  });
  const signInScreener = await verifyAndSignInMonitoring({
    webhookUrl: "https://example.org/exec",
    googleIdToken: validScreenerJwt,
    localStore,
    sessionStore,
    fetchImpl: async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        ok: true,
        authorized: true,
        email: "screener@preservation.example.org",
        role: "screener",
        displayLabel: "Screener",
        assignedAreas: ["area:priority:sunnyside"],
        supportsRoamingSync: true,
        supportsPhase1Monitoring: true,
      }),
    }),
  });
  assert.equal(signInScreener.ok, true);
  assert.equal(signInScreener.switchedAccountOrRole, true);
  assert.equal(localStore.getItem(`${LS_AUTH_CACHE_PREFIX}staff@preservation.example.org`), null);
  assert.equal(canAccessStaffOnly(signInScreener.session), false);

  // Even if a manipulated Screener cache entry contained staffOnly, loadCachedMonitoringState strips/rejects it
  localStore.setItem(
    `${LS_AUTH_CACHE_PREFIX}screener@preservation.example.org`,
    JSON.stringify({ role: "staff", staffOnly: { parcelStrategy: [{ strategy_notes: "LEAK" }] } })
  );
  assert.equal(
    loadCachedMonitoringState("screener@preservation.example.org", "screener", { localStore }),
    null
  );

  // 4. Mid-edit 1-hour JWT expiry queues dirty mutation & replays losslessly after re-auth
  const expiredJwt = makeSyntheticJwt({
    email: "staff@preservation.example.org",
    exp: Math.floor(nowMs / 1000) - 10,
  });
  assert.equal(decodeJwtPayloadUnsafe(expiredJwt).email, "staff@preservation.example.org");
  const expiredSession = {
    authorized: true,
    email: "staff@preservation.example.org",
    role: "staff",
    googleIdToken: expiredJwt,
  };
  assert.equal(isSessionTokenExpired(expiredSession, nowMs), true);

  let expiredPromptFired = false;
  let retriedSuccessfully = false;
  const expiredRpc = await callMonitoringRpc({
    webhookUrl: "https://example.org/exec",
    action: "upsert_parcel_decision",
    body: { parcel_id: "0152300000001" },
    session: expiredSession,
    onTokenExpired: () => {
      expiredPromptFired = true;
    },
    retryFn: async () => {
      retriedSuccessfully = true;
      return { ok: true, savedAfterReauth: true };
    },
  });
  assert.equal(expiredRpc.ok, false);
  assert.equal(expiredRpc.tokenExpired, true);
  assert.equal(expiredRpc.queuedForRetry, true);
  assert.equal(expiredPromptFired, true);
  assert.equal(getPendingAuthRetryCount(), 1);

  const replayed = await replayPendingAuthRetries();
  assert.equal(replayed.length, 1);
  assert.equal(replayed[0].ok, true);
  assert.equal(retriedSuccessfully, true);
  assert.equal(getPendingAuthRetryCount(), 0);

  // 5. Cross-tab storage listener + Sign Out purge
  const listeners = [];
  const fakeWindow = {
    addEventListener(type, fn) {
      if (type === "storage") listeners.push(fn);
    },
    removeEventListener(type, fn) {
      const idx = listeners.indexOf(fn);
      if (idx !== -1) listeners.splice(idx, 1);
    },
  };
  let observedCrossTabSignOut = false;
  const unsub = subscribeCrossTabAuthChanges((info) => {
    if (info.signedOut && info.role === "public") observedCrossTabSignOut = true;
  }, fakeWindow);
  signOutMonitoringSession({ localStore, sessionStore });
  assert.equal(localStore.getItem(LS_ADMIN_SESSION_KEY), null);
  assert.ok(localStore.getItem(LS_CROSS_TAB_BROADCAST_KEY));
  listeners[0]({ key: LS_CROSS_TAB_BROADCAST_KEY });
  assert.equal(observedCrossTabSignOut, true);
  unsub();
  purgePrivateMonitoringStorage({ localStore });

  // 6. monitoringWorkbenchUi enum keys match MONITORING_SCHEMA_CONTRACT 100% and render cleanly without dashes
  for (const k of Object.keys(SIGNIFICANCE_BASIS_LABELS)) {
    assert.ok(MONITORING_SCHEMA_CONTRACT.enums.significanceBases.includes(k), `Invalid basis key: ${k}`);
    const norm = normalizeParcelRecord({ parcel_id: "0152300000001", significance_basis: k });
    assert.equal(norm.significance_basis, k);
  }
  for (const k of Object.keys(INTEGRITY_LABELS)) {
    assert.ok(MONITORING_SCHEMA_CONTRACT.enums.integrityGrades.includes(k), `Invalid integrity key: ${k}`);
    const norm = normalizeParcelRecord({ parcel_id: "0152300000001", integrity: k });
    assert.equal(norm.integrity, k);
  }
  for (const k of Object.keys(ADVOCACY_STATUS_LABELS)) {
    assert.ok(MONITORING_SCHEMA_CONTRACT.enums.advocacyStatuses.includes(k), `Invalid advocacy key: ${k}`);
    const norm = normalizeParcelRecord({ parcel_id: "0152300000001", advocacy_status: k });
    assert.equal(norm.advocacy_status, k);
  }
  for (const k of Object.keys(OUTCOME_LABELS)) {
    if (!k) continue;
    assert.ok(MONITORING_SCHEMA_CONTRACT.enums.outcomes.includes(k), `Invalid outcome key: ${k}`);
  }
  const scJson = serializeStoryCheck(
    Object.fromEntries(STORY_CHECK_STEPS.map((s) => [s.key, { checked: true, note: "Verified" }]))
  );
  const parsedSc = parseStoryCheckJson(scJson);
  assert.equal(STORY_CHECK_STEPS.every((s) => parsedSc[s.key]?.checked === true), true);

  const sampleParcel = normalizeParcelRecord({
    parcel_id: "0420090000001",
    hcad_num: "0420090000001",
    primary_address: "2802 Wheeler Ave",
    year_built: 1928,
    review_status: "assessed",
    recommended_significance: "high",
    significance_level: "high",
    significance_basis: "community_cultural",
    integrity: "good",
    advocacy_status: "active_advocacy",
    threat_stage: 3,
  });
  const resolved = resolveEffectiveParcelAssessment({
    platItem: { hcad: "0420090000001", app: "2026-1001" },
    parcelsById: { "0420090000001": sampleParcel },
  });
  assert.equal(resolved.attentionLevel, "act");
  const sigHtmlScreener = renderParcelSignificanceCardHtml({ assessment: resolved, role: "screener" });
  assert.equal(sigHtmlScreener.includes("approveScreenerSigBtn"), false);
  const resolvedPendingApproval = resolveEffectiveParcelAssessment({
    platItem: { hcad: "0420090000001", app: "2026-1001" },
    review: { tier: "1", derivedFromParcel: true },
    parcelsById: { "0420090000001": { ...sampleParcel, significance_level: "" } },
  });
  assert.equal(resolvedPendingApproval.parcel.significance_level, "");
  assert.equal(resolvedPendingApproval.parcel.recommended_significance, "high");
  const sigHtmlStaff = renderParcelSignificanceCardHtml({
    assessment: resolvedPendingApproval,
    role: "staff",
  });
  assert.equal(sigHtmlStaff.includes("approveScreenerSigBtn"), true);
  const staffCardHtml = renderStaffOnlyStrategyCardHtml({
    parcelId: "0420090000001",
    role: "staff",
    strategyRecord: {
      strategy_notes: "Board note",
      contact_notes: "Contact",
      owner_relationship_notes: "Owner",
    },
    candidateEntities: [{ name: "Bayou Demo LLC", role: "developer_llc" }],
  });
  assert.equal(staffCardHtml.includes("staffOnlyStrategyCard"), true);
  assert.equal(
    renderStaffOnlyStrategyCardHtml({ parcelId: "0420090000001", role: "screener" }),
    ""
  );
  assert.ok(renderSignedOutWatchlistGateHtml().includes("watchlistSignedOutGate"));
  assert.ok(
    renderSourceConflictWritebackCardHtml({
      role: "staff",
      conflictReport: detectParcelSourceConflicts({
        hcadNum: "0420090000001",
        parcelRecord: { hcad_num: "0420090000001", year_built: 1928, hcad_year_built: 1940 },
      }),
    }).includes("publishApprovedOverrideBtn")
  );
  const qItem = classifyModerationQueueItem({
    sourceTab: "Pending_Submissions",
    rowNumber: 2,
    hcad_num: "0420090000001",
    address: "2802 WHEELER AVE",
    suggested_year_built: "1928",
  });
  assert.ok(renderModerationQueueListHtml([qItem], qItem.queueId).includes("2802 WHEELER AVE"));
  assert.ok(renderModerationQueueDetailHtml(qItem).includes('data-mod-decision="Approved"'));
  assert.equal(mintSyntheticUnparceledId({ appNo: "2026-1999" }), "UNPARCELED:APP_2026_1999");
  assert.equal(normalizeHcad13("420090000001"), "0420090000001");
  assert.equal(classifyProtectionLevel("Protected Landmark"), "binding");
  assert.equal(
    computeContributingStatus(
      { year_built: 1955 },
      resolveEffectiveAreaContext("area:priority:sunnyside", {
        "area:priority:sunnyside": { priority_status: "priority", age_rule_cutoff_year: 1976 },
      })
    ),
    "contributing"
  );
});

test("Round 18 Review Regressions: Code.cjs schema/attention parity, deactivated Sheet Editor RBAC block, Screener Watchlist field lock + concurrency, standing Col W polygon preservation, nested GeoJSON tag stripping, and moderation HCAD/year-0 persistence", () => {
  const cjsPath = path.join(REPO_ROOT, "pipeline", "atlas_pipeline", "apps_script", "Code.cjs");
  const exported = requireCjs(cjsPath);

  // 1. Deep structural parity between Code.cjs constants and monitoring_schema_contract.json (issue_100)
  for (const [tableName, contractTable] of Object.entries(MONITORING_SCHEMA_CONTRACT.tables)) {
    const cjsTable = exported.TABLE_REGISTRY[tableName];
    assert.ok(cjsTable, `Code.cjs TABLE_REGISTRY missing table ${tableName}`);
    assert.equal(cjsTable.workbook, contractTable.workbook);
    assert.equal(cjsTable.sheetName, contractTable.sheetName);
    assert.equal(cjsTable.primaryKey, contractTable.primaryKey);
    assert.deepEqual(cjsTable.headers, contractTable.headers, `Header mismatch on ${tableName}`);
  }
  assert.deepEqual(
    Object.keys(exported.PIPELINE_MACHINE_PARCEL_COLS).sort(),
    [...MONITORING_SCHEMA_CONTRACT.columnOwnership.pipelineMachineOwnedParcelCols].sort()
  );
  assert.deepEqual(
    Object.keys(exported.STAFF_ONLY_DECISION_COLS).sort(),
    [...MONITORING_SCHEMA_CONTRACT.columnOwnership.staffOnlyDecisionCols].sort()
  );
  assert.deepEqual(
    Object.keys(exported.TERMINAL_OUTCOMES).sort(),
    [...MONITORING_SCHEMA_CONTRACT.enums.terminalOutcomes].sort()
  );

  // 2. Run ALL shared attentionVectors against Code.cjs computeAttentionServer_ (issue_096)
  const vectorsPath = path.join(REPO_ROOT, "tests", "fixtures", "phase1_monitoring_parity_vectors.json");
  const suite = JSON.parse(fs.readFileSync(vectorsPath, "utf8"));
  for (const vec of suite.attentionVectors) {
    const srvActual = exported.computeAttentionServer_(
      vec.parcel,
      suite.areasById,
      vec.asOfDateCst,
      vec.nextHearingDateCst
    );
    assert.equal(
      srvActual,
      vec.expectedAttention,
      `Code.cjs computeAttentionServer_ mismatch on ${vec.id}: expected ${vec.expectedAttention}, got ${srvActual}`
    );
  }

  // Tombstone custom area cutoff parity (issue_100)
  const tombCustomCutoff = migrateLegacyTierToParcelFields(
    { removedFromWatchlist: true, why: "Demolished on site", yearBuilt: 1980 },
    { isWatchlist: true, areaContext: { areaIds: ["area:custom"], ageRuleCutoffYear: 1985 } }
  );
  assert.equal(tombCustomCutoff.outcome, "lost");

  // 3. Deactivated _Monitoring_Roles user + Screener listed in ss.getEditors() must NOT fail open to Staff (issue_093)
  const nowSec = Math.floor(1795440000000 / 1000);
  const staffJwt = "jwt-r18-staff";
  const screenerJwt = "jwt-r18-screener";
  const inactiveEditorJwt = "jwt-r18-inactive-editor";
  const h = createPhase1AppsScriptHarness({
    editors: [
      "staff@preservation.example.org",
      "screener@preservation.example.org",
      "inactive@preservation.example.org",
    ],
    tokenDirectory: {
      [staffJwt]: {
        claims: {
          aud: "atlas-client-id.apps.googleusercontent.com",
          email: "staff@preservation.example.org",
          email_verified: true,
          exp: String(nowSec + 3600),
        },
      },
      [screenerJwt]: {
        claims: {
          aud: "atlas-client-id.apps.googleusercontent.com",
          email: "screener@preservation.example.org",
          email_verified: true,
          exp: String(nowSec + 3600),
        },
      },
      [inactiveEditorJwt]: {
        claims: {
          aud: "atlas-client-id.apps.googleusercontent.com",
          email: "inactive@preservation.example.org",
          email_verified: true,
          exp: String(nowSec + 3600),
        },
      },
    },
  });
  h.callPost({ action: "bootstrap_phase1_vaults", google_id_token: staffJwt });

  const deactVerify = h.callPost({ action: "verify_admin", google_id_token: inactiveEditorJwt });
  assert.equal(deactVerify.authorized, false, "Deactivated _Monitoring_Roles user in getEditors() must be blocked");
  const deactPasskey = h.callPost({
    action: "verify_admin",
    admin_email: "inactive@preservation.example.org",
    admin_passkey: "staff-passkey-phase1",
  });
  assert.equal(deactPasskey.authorized, false);
  const screenerInEditors = h.callPost({ action: "verify_admin", google_id_token: screenerJwt });
  assert.equal(screenerInEditors.role, "screener", "Screener in getEditors() must stay restricted to screener");

  // 4. Watchlist dossier Screener Staff-only field lock + expected_updated_at conflict guard (issue_094)
  const seedWl = h.callPost({
    action: "upsert_watchlist_dossier",
    google_id_token: staffJwt,
    dossier: {
      id: "prop-r18-1",
      address: "500 Main St",
      significanceLevel: "high",
      advocacyStatus: "active_advocacy",
      outcome: "",
      updatedAt: "2026-11-23T10:00:00.000Z",
    },
  });
  assert.equal(seedWl.ok, true);

  for (const [forbiddenKey, badVal] of [
    ["significanceLevel", "not_significant"],
    ["advocacyStatus", "closed"],
    ["outcome", "not_relevant"],
    ["removedFromWatchlist", true],
  ]) {
    const badScreenerWl = h.callPost({
      action: "upsert_watchlist_dossier",
      google_id_token: screenerJwt,
      dossier: {
        id: "prop-r18-1",
        address: "500 Main St",
        significanceLevel: "high",
        advocacyStatus: "active_advocacy",
        outcome: "",
        [forbiddenKey]: badVal,
      },
    });
    assert.equal(badScreenerWl.ok, false);
    assert.equal(badScreenerWl.authorized, false);
    assert.equal(badScreenerWl.forbiddenField, forbiddenKey);
  }

  const staleWl = h.callPost({
    action: "upsert_watchlist_dossier",
    google_id_token: staffJwt,
    expected_updated_at: "2026-11-20T00:00:00.000Z",
    dossier: { id: "prop-r18-1", address: "500 Main St", updatedAt: "2026-11-23T11:00:00.000Z" },
  });
  assert.equal(staleWl.ok, false);
  assert.equal(staleWl.conflict, true);

  const okScreenerWl = h.callPost({
    action: "upsert_watchlist_dossier",
    google_id_token: screenerJwt,
    expected_updated_at: "2026-11-23T10:00:00.000Z",
    dossier: {
      id: "prop-r18-1",
      address: "500 Main St",
      significanceLevel: "high",
      advocacyStatus: "active_advocacy",
      outcome: "",
      notes: "Screener deed note added",
      updatedAt: "2026-11-23T10:30:00.000Z",
    },
  });
  assert.equal(okScreenerWl.ok, true);

  // 5. upsert_parcel_decision preserves COH Historic District binding protection_level, array significance_basis, and 14-day urgent hearing (issue_096)
  const sixthWardRes = h.callPost({
    action: "upsert_parcel_decision",
    google_id_token: staffJwt,
    parcel_id: "0010010000001",
    as_of_date_cst: "2026-11-23",
    patch: {
      significance_level: "high",
      significance_basis: ["architectural", "community_cultural"],
      threat_stage: 3,
      protection_level: "binding",
      designation_summary: "Old Sixth Ward",
    },
  });
  assert.equal(sixthWardRes.ok, true);
  assert.equal(sixthWardRes.parcel.protection_level, "binding");
  assert.equal(sixthWardRes.parcel.significance_basis, "architectural; community_cultural");
  assert.equal(sixthWardRes.parcel.attention_level, "watch");

  const urgentHearingRes = h.callPost({
    action: "upsert_parcel_decision",
    google_id_token: staffJwt,
    parcel_id: "0010010000002",
    as_of_date_cst: "2026-11-23",
    next_hearing_date_cst: "2026-11-28",
    patch: {
      significance_level: "high",
      threat_stage: 0,
      protection_level: "none",
    },
  });
  assert.equal(urgentHearingRes.ok, true);
  assert.equal(urgentHearingRes.parcel.attention_level, "act");

  // 6. Standing Approved_Edits updates preserve Col W custom polygon & metadata, and nested GeometryCollection tags strip cleanly (issue_095, issue_103)
  const polyJson = JSON.stringify({
    type: "Polygon",
    coordinates: [[[-95.36, 29.75], [-95.35, 29.75], [-95.35, 29.76], [-95.36, 29.75]]],
  });
  const seedStanding = h.callPost({
    action: "admin_approve",
    google_id_token: staffJwt,
    hcad_num: "0621100000099",
    address: "900 BAYLAND AVE",
    suggested_year_built: 1924,
    building_name: "Historic Bungalow",
    bld_style: "Craftsman",
    architect: "Master Builder",
    geometry_geojson: polyJson,
    source_citation: "1924 Permit",
  });
  assert.equal(seedStanding.ok, true);

  // Update year_built via Field_Surveys promotion without passing polygon or architect -> must preserve Col W & architect!
  h.callPost({
    action: "field_survey",
    survey_id: "SURV_R18_KEEP_POLY",
    hcad_num: "0621100000099",
    address: "900 BAYLAND AVE",
    observed_year_or_demo_note: "1926",
    field_notes: "Porch intact",
  });
  const promoteKeepPoly = h.callPost({
    action: "moderate_submission",
    google_id_token: staffJwt,
    sourceTab: "Field_Surveys",
    rowNumber: 2,
    survey_id: "SURV_R18_KEEP_POLY",
    decision: "Approved",
  });
  assert.equal(promoteKeepPoly.ok, true);
  const pendSheet = h.coreWb.getSheetByName("Pending_Submissions");
  assert.equal(pendSheet.getRange(2, 4).getValue(), 1926);
  assert.equal(pendSheet.getRange(2, 23).getValue(), polyJson, "Standing survey promotion must preserve Col W polygon");
  assert.equal(pendSheet.getRange(2, 15).getValue(), "Historic Bungalow");
  assert.equal(pendSheet.getRange(2, 18).getValue(), "Master Builder");

  const nestedGeomCollectionCitation =
    'Field verified [GEOMETRY_GEOJSON: {"geometries":[{"type":"Polygon","coordinates":[[[-95.36,29.75],[-95.35,29.75],[-95.35,29.76],[-95.36,29.75]]]}],"type":"GeometryCollection"}] archival note';
  assert.equal(stripEmbeddedGeometryTagClient(nestedGeomCollectionCitation), "Field verified archival note");

  // Demolish/vacant suppression on row with nested GeoJSON citation strips entire balanced tag cleanly on server
  h.callPost({
    hcad_num: "0123456000001",
    address: "700 DEMO ST",
    suggested_year_built: 1930,
    source_citation: nestedGeomCollectionCitation,
  });
  const modItems = h.callPost({ action: "list_pending_submissions", google_id_token: staffJwt }).items;
  const demoItem = classifyModerationQueueItem(modItems.find((it) => it.address === "700 DEMO ST"));
  // Moderator edits HCAD to #b2 AND types "0" into #mq-year without checking #mq-suppress-vacant (issue_097)
  const modDecisionPayload = buildModerationDecisionPayload(demoItem, "Approved", {
    hcad_num: "123456000001#b2",
    suggested_year_built: "0",
    suppress_only: false,
  });
  assert.equal(modDecisionPayload.suppress_only, true, "Explicit moderator year '0' must set suppress_only: true");
  assert.equal(modDecisionPayload.suggested_year_built, 0);
  const modExec = h.callPost({ ...modDecisionPayload, google_id_token: staffJwt });
  assert.equal(modExec.ok, true);
  assert.equal(pendSheet.getRange(3, 2).getValue(), "0123456000001#b2", "Edited #mq-hcad must persist and zero-pad prefix");
  assert.equal(pendSheet.getRange(3, 4).getValue(), 0);
  assert.equal(pendSheet.getRange(3, 9).getValue(), "Field verified archival note");
});
