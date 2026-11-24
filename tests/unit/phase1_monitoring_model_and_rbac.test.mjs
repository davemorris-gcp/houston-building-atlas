import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

import {
  MONITORING_SCHEMA_CONTRACT,
  advanceSweepThreatStage,
  buildCampusParcelIndex,
  calendarDaysBetween,
  canonicalizeCampusId,
  classifyProtectionLevel,
  compactHistoryJsonFifo,
  compactWatchlistDossierJson,
  computeAttentionLevel,
  computeContributingStatus,
  computePublicPlatSuggestedTier,
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
  preserveStaffOnlyWatchlistFields,
  resolveCanonicalParcelId,
  resolveEffectiveAreaContext,
  sanitizeMonitoringText,
  stripEmbeddedGeometryTag,
  toChicagoCalendarDate,
} from "../../app/js/parcelMonitoringModel.js";

import {
  LS_ADMIN_SESSION_KEY,
  LS_AUTH_CACHE_PREFIX,
  LS_CONFIGURED_GOOGLE_CLIENT_ID_KEY,
  LS_CROSS_TAB_BROADCAST_KEY,
  callMonitoringRpc,
  canAccessStaffOnly,
  clearPendingAuthRetries,
  createParcelSyncCoordinator,
  decodeJwtPayloadUnsafe,
  ensureCleanUnauthenticatedStorageOnBoot,
  getActiveMonitoringRole,
  getPendingAuthRetryCount,
  isSessionTokenExpired,
  loadCachedMonitoringState,
  pullCloudMonitoringState,
  purgePrivateMonitoringStorage,
  queuePendingAuthRetry,
  readActiveMonitoringSession,
  replayPendingAuthRetries,
  resolveConfiguredGoogleClientId,
  saveCachedMonitoringState,
  signOutMonitoringSession,
  subscribeCrossTabAuthChanges,
  verifyAndSignInMonitoring,
} from "../../app/js/monitoringAuthClient.js";

import {
  buildApprovedOverrideWritebackPayload,
  detectParcelSourceConflicts,
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
  hydrateMonitoringParcelAndDecisionMaps,
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

const SHEET_COL_STATUS = 1;
const SHEET_COL_HCAD = 2;
const SHEET_COL_ADDRESS = 3;
const SHEET_COL_YEAR_BUILT = 4;
const SHEET_COL_ORIG_HCAD_YEAR = 5;
const SHEET_COL_DISTRICT = 6;
const SHEET_COL_CONTRIBUTING = 7;
const SHEET_COL_CITATION = 9;
const SHEET_COL_SOURCE_URL = 10;
const SHEET_COL_BUILDING_NAME = 15;
const SHEET_COL_ALT_NAMES = 16;
const SHEET_COL_GEOMETRY_W = 23;

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

function createDeferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function createManualTimerQueue() {
  const scheduled = new Map();
  let nextId = 1;
  return {
    scheduled,
    scheduleTimer: (fn) => {
      const id = nextId++;
      scheduled.set(id, fn);
      return id;
    },
    cancelTimer: (id) => scheduled.delete(id),
    popNextTimer: () => {
      const first = scheduled.entries().next();
      if (first.done) return null;
      const [id, fn] = first.value;
      scheduled.delete(id);
      return fn;
    },
  };
}

function createFakeStorageWindow() {
  const listeners = new Set();
  return {
    windowStub: {
      addEventListener: (type, fn) => {
        if (type === "storage") listeners.add(fn);
      },
      removeEventListener: (type, fn) => {
        if (type === "storage") listeners.delete(fn);
      },
    },
    dispatchStorage: (ev) => {
      for (const fn of Array.from(listeners)) {
        fn(ev);
      }
    },
    listenerCount: () => listeners.size,
  };
}

function makeSerializedSession({ email, role = "staff", expOffsetSec = 3600 }) {
  const exp = Math.floor(Date.now() / 1000) + expOffsetSec;
  return JSON.stringify({
    authorized: true,
    email,
    role,
    googleIdToken: makeSyntheticJwt({ email, exp }),
  });
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
  assert.equal(stripEmbeddedGeometryTag(`Note [GEOMETRY_GEOJSON: ${customPolyGeoJson}] end`), "Note end");

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
    [...exported.STAFF_ONLY_WATCHLIST_DOSSIER_KEYS].sort(),
    [...MONITORING_SCHEMA_CONTRACT.columnOwnership.staffOnlyWatchlistDossierKeys].sort()
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

  // 4. Watchlist dossier Screener Staff-only field lock + server-monotonic expected_updated_at conflict guard (issue_094, Round 19)
  const seedWl = h.callPost({
    action: "upsert_watchlist_dossier",
    google_id_token: staffJwt,
    dossier: {
      id: "prop-r18-1",
      address: "500 Main St",
      significanceLevel: "high",
      advocacyStatus: "active_advocacy",
      attentionLevel: "act",
      outcome: "",
      updatedAt: "2026-11-23T10:00:00.000Z",
    },
  });
  assert.equal(seedWl.ok, true);
  assert.notEqual(
    seedWl.updated_at,
    "2026-11-23T10:00:00.000Z",
    "Server must stamp a fresh monotonic updated_at rather than echoing client updatedAt"
  );
  const pulledAfterSeed = h.callPost({ action: "pull_monitoring_state", google_id_token: staffJwt });
  assert.equal(
    pulledAfterSeed.watchlistDossiers.find((d) => d.id === "prop-r18-1").updatedAt,
    seedWl.updated_at,
    "pull_monitoring_state must hydrate dossier.updatedAt from Watchlist_Dossiers.updated_at"
  );

  for (const [forbiddenKey, badVal] of [
    ["significanceLevel", "not_significant"],
    ["advocacyStatus", "closed"],
    ["outcome", "not_relevant"],
    ["removedFromWatchlist", true],
    ["graduateToLost", true],
    ["contributing_override", "non_contributing"],
    ["attentionLevel", "record"],
  ]) {
    const badScreenerWl = h.callPost({
      action: "upsert_watchlist_dossier",
      google_id_token: screenerJwt,
      dossier: {
        id: "prop-r18-1",
        address: "500 Main St",
        significanceLevel: "high",
        advocacyStatus: "active_advocacy",
        attentionLevel: "act",
        outcome: "",
        [forbiddenKey]: badVal,
      },
    });
    assert.equal(badScreenerWl.ok, false);
    assert.equal(badScreenerWl.authorized, false);
    assert.equal(badScreenerWl.forbiddenField, forbiddenKey);
  }

  const guardedClientDossier = preserveStaffOnlyWatchlistFields(
    {
      id: "prop-r18-1",
      tier: "1",
      trackerStatus: "Lost",
      graduateToLost: true,
      notes: "Screener note",
    },
    null,
    "screener"
  );
  assert.equal("tier" in guardedClientDossier, false);
  assert.equal("graduateToLost" in guardedClientDossier, false);
  assert.equal(guardedClientDossier.notes, "Screener note");
  assert.equal(computePublicPlatSuggestedTier({ oldestYr: 1928, flags: ["hearing"] }), "1");

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
    expected_updated_at: seedWl.updated_at,
    dossier: {
      id: "prop-r18-1",
      address: "500 Main St",
      significanceLevel: "high",
      advocacyStatus: "active_advocacy",
      attentionLevel: "act",
      outcome: "",
      notes: "Screener deed note added",
      updatedAt: seedWl.updated_at,
    },
  });
  assert.equal(okScreenerWl.ok, true);
  assert.notEqual(
    okScreenerWl.updated_at,
    seedWl.updated_at,
    "Subsequent save at identical clock tick must advance updated_at by +1ms"
  );

  // 10-12 digit unnormalized parcel_id sequential saves keep 1:1 Decisions row without lockout (Round 19)
  const shortPidSave1 = h.callPost({
    action: "upsert_parcel_decision",
    google_id_token: staffJwt,
    parcel_id: "420090000055",
    patch: { review_status: "assessed", significance_level: "moderate" },
  });
  assert.equal(shortPidSave1.ok, true);
  assert.equal(shortPidSave1.parcel.parcel_id, "0420090000055");
  const shortPidSave2 = h.callPost({
    action: "upsert_parcel_decision",
    google_id_token: staffJwt,
    parcel_id: "0420090000055",
    expected_updated_at: shortPidSave1.decision.decided_at,
    patch: { screening_notes: "Second save" },
  });
  assert.equal(shortPidSave2.ok, true);
  const shortPidSave3 = h.callPost({
    action: "upsert_parcel_decision",
    google_id_token: staffJwt,
    parcel_id: "0420090000055",
    expected_updated_at: shortPidSave2.decision.decided_at,
    patch: { screening_notes: "Third save" },
  });
  assert.equal(shortPidSave3.ok, true);
  const pulledAfterShort = h.callPost({ action: "pull_monitoring_state", google_id_token: staffJwt });
  assert.equal(
    pulledAfterShort.decisions.filter((d) => d.parcel_id === "0420090000055").length,
    1,
    "Sequential 12-digit then 13-digit saves must maintain exactly 1 Decisions row"
  );

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

  // Promoting a Field_Surveys row with prose observed_year_or_demo_note must NOT wipe Col D year 1926! (Round 19)
  h.callPost({
    action: "field_survey",
    survey_id: "SURV_R19_PROSE_NOTE",
    hcad_num: "0621100000099",
    address: "900 BAYLAND AVE",
    observed_year_or_demo_note: "Original wood siding intact",
    field_notes: "Window trim intact",
  });
  assert.equal(
    h.callPost({
      action: "moderate_submission",
      google_id_token: staffJwt,
      sourceTab: "Field_Surveys",
      rowNumber: 3,
      survey_id: "SURV_R19_PROSE_NOTE",
      decision: "Approved",
    }).ok,
    true
  );
  assert.equal(pendSheet.getRange(2, 4).getValue(), 1926, "Prose survey promotion must preserve standing Col D year");

  // Approving a new Pending_Submissions year-only row on 0621100000099 must carry forward Col W polygon & mark prior row Superseded!
  h.callPost({
    hcad_num: "0621100000099",
    address: "900 BAYLAND AVE",
    suggested_year_built: 1925,
    source_citation: "1925 Directory update",
  });
  const baylandPendRow = h
    .callPost({ action: "list_pending_submissions", google_id_token: staffJwt })
    .items.find((it) => it.sourceTab === "Pending_Submissions" && it.address === "900 BAYLAND AVE");
  assert.equal(
    h.callPost({
      ...buildModerationDecisionPayload(classifyModerationQueueItem(baylandPendRow), "Approved"),
      google_id_token: staffJwt,
    }).ok,
    true
  );
  assert.equal(pendSheet.getRange(2, 1).getValue(), "Superseded");
  assert.equal(pendSheet.getRange(baylandPendRow.rowNumber, 4).getValue(), 1925);
  assert.equal(pendSheet.getRange(baylandPendRow.rowNumber, 23).getValue(), polyJson);
  assert.equal(pendSheet.getRange(baylandPendRow.rowNumber, 15).getValue(), "Historic Bungalow");

  const nestedGeomCollectionCitation =
    'Field verified [GEOMETRY_GEOJSON: {"geometries":[{"type":"Polygon","coordinates":[[[-95.36,29.75],[-95.35,29.75],[-95.35,29.76],[-95.36,29.75]]]}],"type":"GeometryCollection"}] archival note';
  assert.equal(stripEmbeddedGeometryTag(nestedGeomCollectionCitation), "Field verified archival note");

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
  assert.equal(
    pendSheet.getRange(demoItem.rowNumber, 2).getValue(),
    "0123456000001#b2",
    "Edited #mq-hcad must persist and zero-pad prefix"
  );
  assert.equal(pendSheet.getRange(demoItem.rowNumber, 4).getValue(), 0);
  assert.equal(
    pendSheet.getRange(demoItem.rowNumber, 9).getValue(),
    "[DEMOLISHED / VACANT LOT - HIDE FOOTPRINT] Field verified archival note"
  );
});

function createStaffBootstrappedHarness(nowMs = 1795450000000) {
  const nowSec = Math.floor(nowMs / 1000);
  const staffJwt = "jwt-staff-isolated";
  const h = createPhase1AppsScriptHarness({
    nowMs,
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
  h.callPost({ action: "bootstrap_phase1_vaults", google_id_token: staffJwt });
  return { h, staffJwt };
}

test("UT-P1-Mono: pinned clock advances Watchlist_Dossiers.updated_at and Decisions.decided_at strictly monotonically", () => {
  const { h, staffJwt } = createStaffBootstrappedHarness(1795450000000);
  h.setNowMs(1795450000000);
  const t0Iso = new Date(1795450000000).toISOString();
  const t1Iso = new Date(1795450000001).toISOString();
  const t2Iso = new Date(1795450000002).toISOString();

  const wlSave1 = h.callPost({
    action: "upsert_watchlist_dossier",
    google_id_token: staffJwt,
    dossier: { id: "prop-mono", address: "100 Main St", notes: "Save 1" },
  });
  const wlSave2 = h.callPost({
    action: "upsert_watchlist_dossier",
    google_id_token: staffJwt,
    expected_updated_at: wlSave1.updated_at,
    dossier: { id: "prop-mono", address: "100 Main St", notes: "Save 2" },
  });
  const wlSave3 = h.callPost({
    action: "upsert_watchlist_dossier",
    google_id_token: staffJwt,
    expected_updated_at: wlSave2.updated_at,
    dossier: { id: "prop-mono", address: "100 Main St", notes: "Save 3" },
  });
  assert.equal(wlSave1.updated_at, t0Iso);
  assert.equal(wlSave2.updated_at, t1Iso);
  assert.equal(wlSave3.updated_at, t2Iso);

  const decSave1 = h.callPost({
    action: "upsert_parcel_decision",
    google_id_token: staffJwt,
    parcel_id: "0420090000091",
    patch: { review_status: "assessed", significance_level: "moderate", screening_notes: "Tick 1" },
  });
  const decSave2 = h.callPost({
    action: "upsert_parcel_decision",
    google_id_token: staffJwt,
    parcel_id: "0420090000091",
    expected_updated_at: decSave1.decision.decided_at,
    patch: { screening_notes: "Tick 2" },
  });
  const decSave3 = h.callPost({
    action: "upsert_parcel_decision",
    google_id_token: staffJwt,
    parcel_id: "0420090000091",
    expected_updated_at: decSave2.decision.decided_at,
    patch: { screening_notes: "Tick 3" },
  });
  assert.equal(decSave1.decision.decided_at, t0Iso);
  assert.equal(decSave2.decision.decided_at, t1Iso);
  assert.equal(decSave3.decision.decided_at, t2Iso);
});

test("IT-P1-Campus: 12-digit sweep preservation, single-parcel to CAMPUS:<SLUG> in-place promotion, and case-insensitive CAMPUS parity", () => {
  const cjsPath = path.join(REPO_ROOT, "pipeline", "atlas_pipeline", "apps_script", "Code.cjs");
  const exported = requireCjs(cjsPath);
  const { h, staffJwt } = createStaffBootstrappedHarness(1795450000000);

  // 1. Case-insensitive CAMPUS:<SLUG> canonicalization parity across JS and Code.cjs
  for (const variant of [
    { parcelId: "", campusGroupId: "Parish Assemblage" },
    { parcelId: "", campusGroupId: "campus:Parish Assemblage" },
    { parcelId: "", campusGroupId: "CAMPUS:Parish Assemblage" },
    { parcelId: "campus:Parish Assemblage", campusGroupId: "" },
    { parcelId: "CAMPUS:Parish Assemblage", campusGroupId: "" },
    { parcelId: "0420010000001", campusGroupId: "campus:Parish Assemblage" },
  ]) {
    assert.equal(resolveCanonicalParcelId(variant), "CAMPUS:PARISH_ASSEMBLAGE");
  }
  assert.equal(canonicalizeCampusId("campus:Parish Assemblage"), "CAMPUS:PARISH_ASSEMBLAGE");

  // 2. Seed standing single-parcel row '0420010000001' in Parcels + Decisions, then promote in place to CAMPUS:PARISH_ASSEMBLAGE
  const initialSingle = h.callPost({
    action: "upsert_parcel_decision",
    google_id_token: staffJwt,
    parcel_id: "0420010000001",
    patch: {
      hcad_num: "0420010000001",
      primary_address: "1200 ANNEX ST",
      year_built: 1912,
      owner_of_record: "PARISH BOARD",
      protection_level: "binding",
      designation_summary: "Protected Landmark",
      review_status: "assessed",
      significance_level: "high",
      screening_notes: "Initial single-parcel history",
    },
  });
  assert.equal(initialSingle.ok, true);

  // Stale expected_updated_at on campus promotion must still trigger conflict detection!
  const stalePromote = h.callPost({
    action: "upsert_parcel_decision",
    google_id_token: staffJwt,
    parcel_id: "0420010000001",
    expected_updated_at: "2020-01-01T00:00:00.000Z",
    patch: { campus_group_id: "campus:Parish Assemblage" },
  });
  assert.equal(stalePromote.ok, false);
  assert.equal(stalePromote.conflict, true);

  const promotedCampus = h.callPost({
    action: "upsert_parcel_decision",
    google_id_token: staffJwt,
    parcel_id: "0420010000001",
    expected_updated_at: initialSingle.decision.decided_at,
    patch: {
      campus_group_id: "campus:Parish Assemblage",
      related_hcads: "0420010000001; 0420010000002",
      screening_notes: "Promoted to 2-lot campus assemblage",
    },
  });
  assert.equal(promotedCampus.ok, true);
  assert.equal(promotedCampus.parcel.parcel_id, "CAMPUS:PARISH_ASSEMBLAGE");
  assert.equal("_replacePk" in promotedCampus.parcel, false);
  assert.equal("_replacePk" in promotedCampus.decision, false);
  assert.equal(Number(promotedCampus.parcel.year_built), 1912, "In-place campus promotion must preserve year_built");
  assert.equal(promotedCampus.parcel.protection_level, "binding", "In-place campus promotion must preserve protection_level");
  assert.equal(promotedCampus.parcel.owner_of_record, "PARISH BOARD");
  assert.equal(JSON.parse(promotedCampus.decision.history_json).entries.length, 2, "History entries must carry forward");

  // Also verify coalesced client promotion where patch.parcel_id is already CAMPUS:<SLUG> and replace_parcel_id holds the source HCAD!
  const singleBeforeCoalesce = h.callPost({
    action: "upsert_parcel_decision",
    google_id_token: staffJwt,
    parcel_id: "0420020000009",
    patch: {
      hcad_num: "0420020000009",
      primary_address: "1500 ANNEX AVE",
      year_built: 1920,
      protection_level: "binding",
      review_status: "assessed",
      significance_level: "high",
      screening_notes: "Initial single parcel",
    },
  });
  assert.equal(singleBeforeCoalesce.ok, true);
  const coalescedCampus = h.callPost({
    action: "upsert_parcel_decision",
    google_id_token: staffJwt,
    parcel_id: "CAMPUS:ANNEX_CAMPUS",
    expected_updated_at: singleBeforeCoalesce.decision.decided_at,
    patch: {
      parcel_id: "CAMPUS:ANNEX_CAMPUS",
      replace_parcel_id: "0420020000009",
      campus_group_id: "Annex Campus",
      hcad_num: "0420020000009",
      related_hcads: "0420020000009; 0420020000010",
      screening_notes: "Coalesced campus note",
    },
  });
  assert.equal(coalescedCampus.ok, true);
  assert.equal(coalescedCampus.parcel.parcel_id, "CAMPUS:ANNEX_CAMPUS");
  assert.equal(coalescedCampus.parcel.protection_level, "binding");
  assert.equal(JSON.parse(coalescedCampus.decision.history_json).entries.length, 2);

  // Push a sweep on mixed-case CAMPUS:Parish Assemblage AND save a campus Watchlist dossier -> zero duplicate rows!
  assert.equal(
    h.callPost({
      action: "pipeline_push_sweep_results",
      pipeline_token: "pipeline-secret-token-phase1",
      parcels: [
        {
          parcel_id: "CAMPUS:Parish Assemblage",
          campus_group_id: "Parish Assemblage",
          threat_stage: 3,
          protection_level: "none",
          owner_of_record: "",
        },
      ],
    }).ok,
    true
  );
  assert.equal(
    h.callPost({
      action: "upsert_watchlist_dossier",
      google_id_token: staffJwt,
      dossier: {
        id: "prop-parish-campus",
        parcelId: "0420010000001",
        campusGroupId: "Parish Assemblage",
        hcadAccount: "0420010000001; 0420010000002",
        address: "1200 ANNEX ST",
      },
    }).ok,
    true
  );

  // Also seed a legacy 12-digit Parcels row and verify sweep preserves standing metadata
  const parcelsSheet = h.coreWb.getSheetByName("Parcels");
  const pHeaders = exported.TABLE_REGISTRY.Parcels.headers;
  const raw12Row = pHeaders.map((col) => {
    const seedVals = {
      parcel_id: "420090000077",
      hcad_num: "420090000077",
      primary_address: "1810 KANE ST",
      year_built: 1915,
      owner_of_record: "SMITH FAMILY",
      designation_summary: "Old Sixth Ward",
      protection_level: "binding",
      review_status: "assessed",
      significance_level: "high",
      threat_stage: 2,
      advocacy_status: "active_advocacy",
      attention_level: "watch",
      added_at: "2026-01-15T00:00:00.000Z",
      updated_at: "2026-01-15T00:00:00.000Z",
    };
    return seedVals[col] !== undefined ? seedVals[col] : "";
  });
  parcelsSheet.getRange(parcelsSheet.getLastRow() + 1, 1, 1, pHeaders.length).setValues([raw12Row]);
  assert.equal(
    h.callPost({
      action: "pipeline_push_sweep_results",
      pipeline_token: "pipeline-secret-token-phase1",
      parcels: [
        {
          parcel_id: "0420090000077",
          protection_level: "none",
          owner_of_record: "",
          significance_level: "",
          review_status: "not_reviewed",
          threat_stage: 4,
        },
      ],
    }).ok,
    true
  );

  const stateAfter = h.callPost({ action: "pull_monitoring_state", google_id_token: staffJwt });
  assert.equal(stateAfter.parcels.length, 3, "Parcels sheet must hold 2 in-place promoted campus rows + 1 12-digit row");
  assert.equal(stateAfter.decisions.length, 2, "Decisions sheet must hold 2 in-place promoted campus decision rows");
  const campRow = stateAfter.parcels.find((p) => p.parcel_id === "CAMPUS:PARISH_ASSEMBLAGE");
  assert.equal(campRow.protection_level, "binding");
  assert.equal(campRow.owner_of_record, "PARISH BOARD");
  assert.equal(Number(campRow.threat_stage), 3);
  const kaneRow = stateAfter.parcels.find((p) => p.parcel_id === "0420090000077");
  assert.equal(kaneRow.significance_level, "high");
  assert.equal(kaneRow.protection_level, "binding");
  assert.equal(kaneRow.owner_of_record, "SMITH FAMILY");
  assert.equal(kaneRow.added_at, "2026-01-15T00:00:00.000Z");
  const wlGrid = h.coreWb.getSheetByName("Watchlist_Dossiers").getDataRange().getValues();
  const campWl = wlGrid.find((r) => r[0] === "prop-parish-campus");
  assert.equal(String(campWl[1]), "CAMPUS:PARISH_ASSEMBLAGE");
});

test("IT-P1-Reopen: reopening a closed parcel resets threat_stage 6 to 0 across JS and Code.cjs and restores urgent hearing act escalation", () => {
  const { h, staffJwt } = createStaffBootstrappedHarness(1795450000000);

  // 1. JS normalizeParcelRecord resets threat_stage 6 -> 0 when reopened
  const closedNorm = normalizeParcelRecord({
    parcel_id: "0420090000012",
    significance_level: "high",
    advocacy_status: "closed",
    outcome: "saved",
    threat_stage: 3,
  });
  assert.equal(closedNorm.threat_stage, 6);
  const reopenedNorm = normalizeParcelRecord(
    {
      ...closedNorm,
      advocacy_status: "active_advocacy",
      outcome: "",
      threat_stage: 6,
      next_hearing_date_cst: "2026-11-28",
    },
    { asOfDateCst: "2026-11-23" }
  );
  assert.equal(reopenedNorm.threat_stage, 0);
  assert.equal(reopenedNorm.attention_level, "act");

  // 2. Code.cjs handleUpsertParcelDecisionAction_ resets threat_stage 6 -> 0 when Staff reopens parcel
  const closedSrv = h.callPost({
    action: "upsert_parcel_decision",
    google_id_token: staffJwt,
    parcel_id: "0420090000012",
    as_of_date_cst: "2026-11-23",
    patch: {
      review_status: "assessed",
      significance_level: "high",
      advocacy_status: "closed",
      outcome: "no_longer_threatened",
    },
  });
  assert.equal(closedSrv.parcel.threat_stage, 6);
  assert.equal(closedSrv.parcel.attention_level, "record");

  const reopenedSrv = h.callPost({
    action: "upsert_parcel_decision",
    google_id_token: staffJwt,
    parcel_id: "0420090000012",
    expected_updated_at: closedSrv.decision.decided_at,
    as_of_date_cst: "2026-11-23",
    next_hearing_date_cst: "2026-11-28",
    patch: {
      advocacy_status: "active_advocacy",
      outcome: "",
      threat_stage: 6,
    },
  });
  assert.equal(reopenedSrv.parcel.threat_stage, 0);
  assert.equal(reopenedSrv.parcel.attention_level, "act");
  assert.equal(advanceSweepThreatStage({ outcome: "moved", advocacy_status: "monitoring", threat_stage: 6 }, 3), 6);
  assert.equal(advanceSweepThreatStage({ outcome: "", advocacy_status: "active_advocacy", threat_stage: 6 }, 3), 3);

  // 3. Full-seed / new-parcel pipeline_push_sweep_results sets CAMPUS campus_group_id and resets reopened stage 6 -> 0
  assert.equal(
    h.callPost({
      action: "pipeline_push_sweep_results",
      google_id_token: staffJwt,
      allowFullMigrationSeed: true,
      parcels: [
        {
          parcel_id: "campus:Seeded Campus",
          significance_level: "high",
          advocacy_status: "active_advocacy",
          outcome: "",
          threat_stage: 6,
          next_hearing_date_cst: "2026-11-25",
        },
      ],
    }).ok,
    true
  );
  const seededPull = h.callPost({ action: "pull_monitoring_state", google_id_token: staffJwt });
  const seededCamp = seededPull.parcels.find((p) => p.parcel_id === "CAMPUS:SEEDED_CAMPUS");
  assert.equal(seededCamp.campus_group_id, "CAMPUS:SEEDED_CAMPUS");
  assert.equal(Number(seededCamp.threat_stage), 0);
  assert.equal(seededCamp.attention_level, "act");
});

test("IT-P1-Moderation: unified Pending_Submissions and Field_Surveys merge preserves standing years on N/A/None, suppresses Demolished-vacant while keeping district/HCAD cols, splits aliases, and supersedes duplicates", () => {
  const { h, staffJwt } = createStaffBootstrappedHarness(1795450000000);
  const polyGeo = JSON.stringify({
    type: "Polygon",
    coordinates: [[[-95.37, 29.74], [-95.36, 29.74], [-95.36, 29.75], [-95.37, 29.74]]],
  });
  const pendSheet = h.coreWb.getSheetByName("Pending_Submissions");

  // Seed duplicate legacy Approved row + active Approved row
  const dupRowIdx = pendSheet.getLastRow() + 1;
  pendSheet
    .getRange(dupRowIdx, 1, 1, 18)
    .setValues([
      [
        "Approved",
        "'0621100000088",
        "912 BAYLAND AVE",
        1922,
        1930,
        "Norhill",
        "Contributing",
        "Sanborn Map",
        "1924 Sanborn",
        "https://texashistory.unt.edu/ark:/67531/old/",
        "Staff",
        "Researcher",
        "2026-01-01",
        "Initial",
        "Old Name",
        "",
        "Bungalow",
        "",
      ],
    ]);
  const secondApprovedIdx = pendSheet.getLastRow() + 1;
  pendSheet
    .getRange(secondApprovedIdx, 1, 1, 18)
    .setValues([
      [
        "Approved",
        "'0621100000088",
        "912 BAYLAND AVE",
        1926,
        1930,
        "Norhill",
        "Contributing",
        "Sanborn Map",
        "1926 Permit",
        "https://texashistory.unt.edu/ark:/67531/metapth123/",
        "Staff",
        "Researcher",
        "2026-02-01",
        "Standing",
        "Bayland Craftsman",
        "",
        "Craftsman",
        "Architect A",
      ],
    ]);
  pendSheet.getRange(secondApprovedIdx, 23, 1, 1).setValues([[polyGeo]]);

  // 1. Field_Surveys with "N/A" preserves standing year 1926, source_url, and Col W polygon, and marks dupRowIdx Superseded
  h.callPost({
    action: "field_survey",
    survey_id: "SURV_MOD_NA",
    hcad_num: "0621100000088",
    address: "912 BAYLAND AVE",
    observed_year_or_demo_note: "N/A",
    field_notes: "Intact front gable",
  });
  assert.equal(
    h.callPost({
      action: "moderate_submission",
      google_id_token: staffJwt,
      sourceTab: "Field_Surveys",
      rowNumber: h.coreWb.getSheetByName("Field_Surveys").getLastRow(),
      survey_id: "SURV_MOD_NA",
      decision: "Approved",
    }).ok,
    true
  );
  assert.equal(pendSheet.getRange(dupRowIdx, SHEET_COL_STATUS).getValue(), "Superseded");
  assert.equal(pendSheet.getRange(secondApprovedIdx, SHEET_COL_YEAR_BUILT).getValue(), 1926);
  assert.equal(pendSheet.getRange(secondApprovedIdx, SHEET_COL_SOURCE_URL).getValue(), "https://texashistory.unt.edu/ark:/67531/metapth123/");
  assert.equal(pendSheet.getRange(secondApprovedIdx, SHEET_COL_GEOMETRY_W).getValue(), polyGeo);

  // 2. Pending_Submissions row with suggested_year_built = "N/A" AND building_name = "Bayland House; Historic Alias" preserves 1926, source_url, and polygon, and splits primaryName / altNames!
  h.callPost({
    hcad_num: "0621100000088",
    address: "912 BAYLAND AVE",
    suggested_year_built: "N/A",
    building_name: "Bayland House; Historic Alias",
    source_citation: "1928 City Directory",
  });
  const pendNaItem = h
    .callPost({ action: "list_pending_submissions", google_id_token: staffJwt })
    .items.find((it) => it.sourceTab === "Pending_Submissions" && it.address === "912 BAYLAND AVE");
  assert.equal(
    h.callPost({
      action: "moderate_submission",
      google_id_token: staffJwt,
      sourceTab: "Pending_Submissions",
      rowNumber: pendNaItem.rowNumber,
      expected_hcad_num: "0621100000088",
      decision: "Approved",
    }).ok,
    true
  );
  assert.equal(pendSheet.getRange(secondApprovedIdx, SHEET_COL_STATUS).getValue(), "Superseded");
  assert.equal(pendSheet.getRange(pendNaItem.rowNumber, SHEET_COL_YEAR_BUILT).getValue(), 1926, "Pending_Submissions 'N/A' must preserve standing year 1926");
  assert.equal(pendSheet.getRange(pendNaItem.rowNumber, SHEET_COL_SOURCE_URL).getValue(), "https://texashistory.unt.edu/ark:/67531/metapth123/");
  assert.equal(pendSheet.getRange(pendNaItem.rowNumber, SHEET_COL_BUILDING_NAME).getValue(), "Bayland House");
  assert.equal(pendSheet.getRange(pendNaItem.rowNumber, SHEET_COL_ALT_NAMES).getValue(), "Historic Alias");
  assert.equal(pendSheet.getRange(pendNaItem.rowNumber, SHEET_COL_GEOMETRY_W).getValue(), polyGeo);

  // 3. Pending_Submissions row with suggested_year_built = "Demolished - vacant lot" (without suppress_only: true) suppresses year=0 and Col W="" while preserving Cols C/E/F/G!
  h.callPost({
    hcad_num: "0621100000088",
    suggested_year_built: "Demolished - vacant lot",
    source_citation: "Confirmed cleared lot",
  });
  const pendDemoItem = h
    .callPost({ action: "list_pending_submissions", google_id_token: staffJwt })
    .items.find((it) => it.sourceTab === "Pending_Submissions" && it.rowNumber > pendNaItem.rowNumber);
  assert.equal(
    h.callPost({
      action: "moderate_submission",
      google_id_token: staffJwt,
      sourceTab: "Pending_Submissions",
      rowNumber: pendDemoItem.rowNumber,
      expected_hcad_num: "0621100000088",
      decision: "Approved",
    }).ok,
    true
  );
  assert.equal(pendSheet.getRange(pendDemoItem.rowNumber, SHEET_COL_ADDRESS).getValue(), "912 BAYLAND AVE");
  assert.equal(pendSheet.getRange(pendDemoItem.rowNumber, SHEET_COL_YEAR_BUILT).getValue(), 0);
  assert.equal(pendSheet.getRange(pendDemoItem.rowNumber, SHEET_COL_ORIG_HCAD_YEAR).getValue(), 1930);
  assert.equal(pendSheet.getRange(pendDemoItem.rowNumber, SHEET_COL_DISTRICT).getValue(), "Norhill");
  assert.equal(pendSheet.getRange(pendDemoItem.rowNumber, SHEET_COL_CONTRIBUTING).getValue(), "Contributing");
  assert.equal(pendSheet.getRange(pendDemoItem.rowNumber, SHEET_COL_BUILDING_NAME).getValue(), "");
  assert.equal(pendSheet.getRange(pendDemoItem.rowNumber, SHEET_COL_GEOMETRY_W).getValue(), "");

  // 4. Field_Surveys vacant promotion on another standing override also preserves Cols C/E/F/G while clearing Col D=0 and Col W=""
  assert.equal(
    h.callPost({
      action: "admin_approve",
      google_id_token: staffJwt,
      hcad_num: "0621100000089",
      address: "914 BAYLAND AVE",
      suggested_year_built: 1924,
      original_hcad_year: 1932,
      historic_district: "Norhill",
      contributing: "Contributing",
      building_name: "Neighbor Bungalow",
      geometry_geojson: polyGeo,
      source_citation: "1924 Sanborn",
    }).ok,
    true
  );
  h.callPost({
    action: "field_survey",
    survey_id: "SURV_DEMO_KEEP_DIST",
    hcad_num: "0621100000089",
    address: "914 BAYLAND AVE",
    observed_year_or_demo_note: "Demolished - vacant lot",
    field_notes: "Cleared lot",
  });
  const promDemoRes = h.callPost({
    action: "moderate_submission",
    google_id_token: staffJwt,
    sourceTab: "Field_Surveys",
    rowNumber: h.coreWb.getSheetByName("Field_Surveys").getLastRow(),
    survey_id: "SURV_DEMO_KEEP_DIST",
    decision: "Approved",
  });
  assert.equal(promDemoRes.ok, true);
  const allPendRows = pendSheet.getDataRange().getValues();
  const row914Idx = allPendRows.findIndex((r) => String(r[1]).replace(/^'/, "") === "0621100000089" && r[0] === "Approved") + 1;
  assert.equal(pendSheet.getRange(row914Idx, SHEET_COL_ADDRESS).getValue(), "914 BAYLAND AVE");
  assert.equal(pendSheet.getRange(row914Idx, SHEET_COL_YEAR_BUILT).getValue(), 0);
  assert.equal(pendSheet.getRange(row914Idx, SHEET_COL_ORIG_HCAD_YEAR).getValue(), 1932);
  assert.equal(pendSheet.getRange(row914Idx, SHEET_COL_DISTRICT).getValue(), "Norhill");
  assert.equal(pendSheet.getRange(row914Idx, SHEET_COL_CONTRIBUTING).getValue(), "Contributing");
  assert.equal(pendSheet.getRange(row914Idx, SHEET_COL_GEOMETRY_W).getValue(), "");
});

test("IT-P1-Moderation-ZeroSurveyAndInlineYearOverride: atomic Field_Surveys promotion, '0'/'Cleared vacant lot' suppression, and inline standing-year override", () => {
  const { h, staffJwt } = createStaffBootstrappedHarness(1795450000000);
  const polyGeo = JSON.stringify({
    type: "Polygon",
    coordinates: [[[-95.37, 29.74], [-95.36, 29.74], [-95.36, 29.75], [-95.37, 29.74]]],
  });
  const pendSheet = h.coreWb.getSheetByName("Pending_Submissions");
  const fsSheet = h.coreWb.getSheetByName("Field_Surveys");

  // 1. Atomic Field_Surveys failure: missing/invalid HCAD keeps Field_Surveys Col C 'Unreviewed'
  h.callPost({
    action: "field_survey",
    survey_id: "SURV_MISSING_HCAD",
    hcad_num: "",
    address: "915 INVALID HCAD AVE",
    observed_year_or_demo_note: "1924",
  });
  const badRowNum = fsSheet.getLastRow();
  const failedProm = h.callPost({
    action: "moderate_submission",
    google_id_token: staffJwt,
    sourceTab: "Field_Surveys",
    rowNumber: badRowNum,
    survey_id: "SURV_MISSING_HCAD",
    decision: "Approved",
  });
  assert.equal(failedProm.ok, false);
  assert.equal(fsSheet.getRange(badRowNum, 3).getValue(), "Unreviewed");

  // 2. Valid Field_Surveys promotion forwards alt_names and geometry_geojson
  h.callPost({
    action: "field_survey",
    survey_id: "SURV_WITH_ALT_AND_GEOM",
    hcad_num: "0621100000095",
    address: "915 BAYLAND AVE",
    observed_year_or_demo_note: "1924",
  });
  const validProm = h.callPost({
    action: "moderate_submission",
    google_id_token: staffJwt,
    sourceTab: "Field_Surveys",
    rowNumber: fsSheet.getLastRow(),
    survey_id: "SURV_WITH_ALT_AND_GEOM",
    decision: "Approved",
    building_name: "Bayland Cottage",
    alt_names: "Historic Cottage Alias",
    geometry_geojson: polyGeo,
  });
  assert.equal(validProm.ok, true);
  assert.equal(fsSheet.getRange(fsSheet.getLastRow(), 3).getValue(), "Promoted to Override");
  const row915Idx =
    pendSheet
      .getDataRange()
      .getValues()
      .findIndex(
        (r) =>
          String(r[SHEET_COL_HCAD - 1]).replace(/^'/, "") === "0621100000095" &&
          r[SHEET_COL_STATUS - 1] === "Approved"
      ) + 1;
  assert.equal(pendSheet.getRange(row915Idx, SHEET_COL_ALT_NAMES).getValue(), "Historic Cottage Alias");
  assert.equal(pendSheet.getRange(row915Idx, SHEET_COL_GEOMETRY_W).getValue(), polyGeo);

  // 3. Field_Surveys with observed_year_or_demo_note = "0" (omitting suppress_only) suppresses year=0 and clears Col W
  assert.equal(
    h.callPost({
      action: "admin_approve",
      google_id_token: staffJwt,
      hcad_num: "0621100000090",
      address: "916 BAYLAND AVE",
      suggested_year_built: 1925,
      original_hcad_year: 1931,
      historic_district: "Norhill",
      contributing: "Contributing",
      geometry_geojson: polyGeo,
      source_citation: "1925 Permit",
    }).ok,
    true
  );
  h.callPost({
    action: "field_survey",
    survey_id: "SURV_ZERO_NOTE",
    hcad_num: "0621100000090",
    address: "916 BAYLAND AVE",
    observed_year_or_demo_note: "0",
    field_notes: "Slab only",
  });
  assert.equal(
    h.callPost({
      action: "moderate_submission",
      google_id_token: staffJwt,
      sourceTab: "Field_Surveys",
      rowNumber: fsSheet.getLastRow(),
      survey_id: "SURV_ZERO_NOTE",
      decision: "Approved",
    }).ok,
    true
  );
  const row916Idx =
    pendSheet
      .getDataRange()
      .getValues()
      .findIndex(
        (r) =>
          String(r[SHEET_COL_HCAD - 1]).replace(/^'/, "") === "0621100000090" &&
          r[SHEET_COL_STATUS - 1] === "Approved"
      ) + 1;
  assert.equal(pendSheet.getRange(row916Idx, SHEET_COL_YEAR_BUILT).getValue(), 0);
  assert.equal(pendSheet.getRange(row916Idx, SHEET_COL_GEOMETRY_W).getValue(), "");

  // 4. UI buildModerationDecisionPayload on "Cleared vacant lot" suppresses by default, OR preserves explicit inline standing year ("1928")
  assert.equal(
    h.callPost({
      action: "admin_approve",
      google_id_token: staffJwt,
      hcad_num: "0621100000091",
      address: "918 BAYLAND AVE",
      suggested_year_built: 1928,
      original_hcad_year: 1933,
      historic_district: "Norhill",
      contributing: "Contributing",
      geometry_geojson: polyGeo,
      source_citation: "1928 Permit",
    }).ok,
    true
  );
  h.callPost({
    action: "field_survey",
    survey_id: "SURV_CLEARED_LOT_PHRASE",
    hcad_num: "0621100000091",
    address: "918 BAYLAND AVE",
    observed_year_or_demo_note: "Cleared vacant lot",
    field_notes: "Observed cleared grass lot",
  });
  const clearedQueueItem = classifyModerationQueueItem(
    h.callPost({ action: "list_pending_submissions", google_id_token: staffJwt }).items.find(
      (it) => it.survey_id === "SURV_CLEARED_LOT_PHRASE"
    )
  );
  assert.equal(clearedQueueItem.isVacantReport, true);

  const overrideWithStandingYear = buildModerationDecisionPayload(clearedQueueItem, "Approved", {
    suggested_year_built: "1928",
    suppress_only: true,
    geometry_geojson: polyGeo,
  });
  assert.equal(overrideWithStandingYear.suggested_year_built, 1928);
  assert.equal(overrideWithStandingYear.suppress_only, false);
  assert.equal(overrideWithStandingYear.geometry_geojson, polyGeo);

  // Also verify end-to-end against Code.cjs on a second vacant survey overridden with standing year 1928!
  h.callPost({
    action: "field_survey",
    survey_id: "SURV_VACANT_OVERRIDDEN_TO_STANDING",
    hcad_num: "0621100000092",
    address: "920 BAYLAND AVE",
    observed_year_or_demo_note: "Cleared vacant lot",
    field_notes: "False alarm - house still standing behind fence",
  });
  const standingOverrideItem = classifyModerationQueueItem(
    h.callPost({ action: "list_pending_submissions", google_id_token: staffJwt }).items.find(
      (it) => it.survey_id === "SURV_VACANT_OVERRIDDEN_TO_STANDING"
    )
  );
  assert.equal(
    h.callPost({
      ...buildModerationDecisionPayload(standingOverrideItem, "Approved", {
        suggested_year_built: "1928",
        suppress_only: true,
        geometry_geojson: polyGeo,
      }),
      google_id_token: staffJwt,
    }).ok,
    true
  );
  const row920Idx =
    pendSheet
      .getDataRange()
      .getValues()
      .findIndex(
        (r) =>
          String(r[SHEET_COL_HCAD - 1]).replace(/^'/, "") === "0621100000092" &&
          r[SHEET_COL_STATUS - 1] === "Approved"
      ) + 1;
  assert.equal(pendSheet.getRange(row920Idx, SHEET_COL_YEAR_BUILT).getValue(), 1928);
  assert.equal(pendSheet.getRange(row920Idx, SHEET_COL_GEOMETRY_W).getValue(), polyGeo);

  assert.equal(
    h.callPost({
      ...buildModerationDecisionPayload(clearedQueueItem, "Approved"),
      google_id_token: staffJwt,
    }).ok,
    true
  );
  const row918Idx =
    pendSheet
      .getDataRange()
      .getValues()
      .findIndex(
        (r) =>
          String(r[SHEET_COL_HCAD - 1]).replace(/^'/, "") === "0621100000091" &&
          r[SHEET_COL_STATUS - 1] === "Approved"
      ) + 1;
  assert.equal(pendSheet.getRange(row918Idx, SHEET_COL_YEAR_BUILT).getValue(), 0);
  assert.equal(pendSheet.getRange(row918Idx, SHEET_COL_GEOMETRY_W).getValue(), "");
});

test("UT-P1-Tags: server tag scanner preserves trailing citations on malformed tags and strips coordinate-array GeoJSON tags", () => {
  const { h, staffJwt } = createStaffBootstrappedHarness(1795450000000);
  const pendSheet = h.coreWb.getSheetByName("Pending_Submissions");

  h.callPost({
    hcad_num: "0123456000088",
    address: "801 MALFORMED TAG ST",
    suggested_year_built: 1930,
    source_citation: '[GEOMETRY_GEOJSON: {"type":"Polygon" 1926 Sanborn Vol 3 Sheet 42',
  });
  h.callPost({
    hcad_num: "0123456000089",
    address: "803 ARRAY TAG ST",
    suggested_year_built: 1930,
    source_citation: "[GEOMETRY_GEOJSON: [[-95.36,29.75],[-95.35,29.75],[-95.35,29.76],[-95.36,29.75]]] 1926 Sanborn",
  });
  const tagItems = h.callPost({ action: "list_pending_submissions", google_id_token: staffJwt }).items;
  const malformedItem = classifyModerationQueueItem(tagItems.find((it) => it.address === "801 MALFORMED TAG ST"));
  const arrayItem = classifyModerationQueueItem(tagItems.find((it) => it.address === "803 ARRAY TAG ST"));
  assert.equal(
    h.callPost({
      ...buildModerationDecisionPayload(malformedItem, "Approved", { suppress_only: true }),
      google_id_token: staffJwt,
    }).ok,
    true
  );
  assert.equal(
    h.callPost({
      ...buildModerationDecisionPayload(arrayItem, "Approved", { suppress_only: true }),
      google_id_token: staffJwt,
    }).ok,
    true
  );
  assert.equal(
    pendSheet.getRange(malformedItem.rowNumber, SHEET_COL_CITATION).getValue(),
    '[DEMOLISHED / VACANT LOT - HIDE FOOTPRINT] [GEOMETRY_GEOJSON: {"type":"Polygon" 1926 Sanborn Vol 3 Sheet 42',
    "Malformed unclosed tag must never truncate trailing human citation text"
  );
  assert.equal(
    pendSheet.getRange(arrayItem.rowNumber, SHEET_COL_CITATION).getValue(),
    "[DEMOLISHED / VACANT LOT - HIDE FOOTPRINT] 1926 Sanborn"
  );
});

test("UT-P1-SyncCoord: createParcelSyncCoordinator coalesces debounced edits, protects in-flight edits from overlapping pulls, and chains expired-token retries sequentially", async () => {
  const scheduled = new Map();
  let nextTimerId = 1;
  const rpcLog = [];
  const successCalls = [];
  let deferredRpc1Resolve = null;
  let tokenExpiredMode = false;
  const queuedRetries = [];
  let midReplayProtected = false;

  const coord = createParcelSyncCoordinator({
    debounceMs: 650,
    scheduleTimer: (fn) => {
      const id = nextTimerId++;
      scheduled.set(id, fn);
      return id;
    },
    cancelTimer: (id) => {
      scheduled.delete(id);
    },
    sendDecisionRpc: async ({ parcelId, expectedUpdatedAt, patch, retryFn }) => {
      rpcLog.push({ parcelId, expectedUpdatedAt, patch });
      if (tokenExpiredMode) {
        queuedRetries.push(retryFn);
        return { ok: false, tokenExpired: true, queuedForRetry: true };
      }
      if (rpcLog.length === 1) {
        return await new Promise((resolve) => {
          deferredRpc1Resolve = resolve;
        });
      }
      midReplayProtected = coord.hasPendingOrInFlight(parcelId);
      const nextTs = `2026-11-23T12:00:0${rpcLog.length}.000Z`;
      return {
        ok: true,
        parcel: { parcel_id: parcelId, ...patch },
        decision: { parcel_id: parcelId, ...patch, decided_at: nextTs },
      };
    },
    onSuccess: (resp, meta) => {
      successCalls.push({ resp, meta });
    },
  });

  const baseTs1 = coord.captureBaselineExpectedTs("0420090000088", "");
  assert.equal(baseTs1, "");
  coord.enqueue("0420090000088", { screening_notes: "Edit 1" }, baseTs1);
  assert.equal(coord.hasPendingOrInFlight("0420090000088"), true);

  const [t1Id, t1Fn] = Array.from(scheduled.entries())[0];
  scheduled.delete(t1Id);
  const t1Promise = t1Fn();
  assert.equal(rpcLog.length, 1);
  assert.equal(rpcLog[0].expectedUpdatedAt, "");
  assert.equal(coord.hasPendingOrInFlight("0420090000088"), true);

  coord.recordHydratedTimestamp("0420090000088", "2026-11-20T00:00:00.000Z", { fromCloud: true });
  assert.equal(coord.getServerTimestamp("0420090000088"), "");

  const baseTs2 = coord.captureBaselineExpectedTs("0420090000088", "2026-11-23T99:99:99.000Z");
  assert.equal(baseTs2, "");
  coord.enqueue("0420090000088", { significance_level: "high" }, baseTs2);

  const [t2Id, t2Fn] = Array.from(scheduled.entries())[0];
  scheduled.delete(t2Id);
  const t2Promise = t2Fn();
  assert.equal(rpcLog.length, 1, "Timer 2 must wait for in-flight RPC 1 before dispatching");

  deferredRpc1Resolve({
    ok: true,
    parcel: { parcel_id: "0420090000088", screening_notes: "Edit 1" },
    decision: { parcel_id: "0420090000088", screening_notes: "Edit 1", decided_at: "2026-11-23T12:00:01.000Z" },
  });
  await t1Promise;
  await t2Promise;
  assert.equal(successCalls[0].meta.hasNewerPendingEdit, true);
  assert.equal(successCalls[1].meta.hasNewerPendingEdit, false);
  assert.equal(rpcLog.length, 2);
  assert.equal(rpcLog[1].expectedUpdatedAt, "2026-11-23T12:00:01.000Z");
  assert.equal(coord.getServerTimestamp("0420090000088"), "2026-11-23T12:00:02.000Z");
  assert.equal(coord.hasPendingOrInFlight("0420090000088"), false);

  coord.recordHydratedTimestamp("0420090000088", "2026-11-23T12:00:01.000Z", { fromCloud: true });
  assert.equal(coord.getServerTimestamp("0420090000088"), "2026-11-23T12:00:02.000Z");

  tokenExpiredMode = true;
  coord.enqueue("0420090000088", { screening_notes: "Expired Edit A" }, coord.getServerTimestamp("0420090000088"));
  const [t3Id, t3Fn] = Array.from(scheduled.entries())[0];
  scheduled.delete(t3Id);
  await t3Fn();

  coord.enqueue("0420090000088", { advocacy_status: "active_advocacy" }, coord.getServerTimestamp("0420090000088"));
  const [t4Id, t4Fn] = Array.from(scheduled.entries())[0];
  scheduled.delete(t4Id);
  const t4Promise = t4Fn();
  assert.equal(queuedRetries.length, 1, "Follow-up timer on same parcel serializes behind queued retry ticket");
  assert.equal(coord.hasPendingOrInFlight("0420090000088"), true);

  coord.recordHydratedTimestamp("0420090000088", "2026-11-19T00:00:00.000Z", { fromCloud: true });
  assert.equal(coord.getServerTimestamp("0420090000088"), "2026-11-23T12:00:02.000Z");

  tokenExpiredMode = false;
  midReplayProtected = false;
  const rA = await queuedRetries[0]();
  assert.equal(midReplayProtected, true, "hasPendingOrInFlight must stay true during in-flight replay");
  const rB = await t4Promise;
  assert.equal(rA.ok, true);
  assert.equal(rB.ok, true);
  assert.equal(successCalls[2].meta.hasNewerPendingEdit, true);
  assert.equal(successCalls[3].meta.hasNewerPendingEdit, false);
  assert.equal(coord.hasPendingOrInFlight("0420090000088"), false);
  assert.equal(successCalls.length, 4);
  assert.equal(rpcLog[rpcLog.length - 2].expectedUpdatedAt, "2026-11-23T12:00:02.000Z");
  assert.equal(rpcLog[rpcLog.length - 1].expectedUpdatedAt, rA.decision.decided_at);
});

test("UT-P1-SyncCoord-409Conflict: refreshes server timestamp on 409 with currentDecision and clears it when row is absent", async () => {
  const scheduled = new Map();
  let nextTimerId = 1;
  const conflicts = [];
  let rpcResponder = async () => ({ ok: true });

  const coord = createParcelSyncCoordinator({
    debounceMs: 650,
    scheduleTimer: (fn) => {
      const id = nextTimerId++;
      scheduled.set(id, fn);
      return id;
    },
    cancelTimer: (id) => scheduled.delete(id),
    sendDecisionRpc: async (args) => await rpcResponder(args),
    onConflict: (resp) => conflicts.push(resp),
  });

  coord.recordHydratedTimestamp("0420010000001", "2026-11-23T10:00:00.000Z", { fromCloud: true });
  rpcResponder = async () => ({
    ok: false,
    conflict: true,
    currentDecision: { parcel_id: "0420010000001", decided_at: "2026-11-23T15:00:00.000Z" },
  });
  coord.enqueue("0420010000001", { screening_notes: "Stale edit" }, "2026-11-23T10:00:00.000Z");
  const [c1Id, c1Fn] = Array.from(scheduled.entries())[0];
  scheduled.delete(c1Id);
  await c1Fn();
  assert.equal(conflicts.length, 1);
  assert.equal(coord.getServerTimestamp("0420010000001"), "2026-11-23T15:00:00.000Z");

  rpcResponder = async () => ({ ok: false, conflict: true, currentDecision: null });
  coord.enqueue("0420010000001", { screening_notes: "Deleted row conflict" }, "2026-11-23T15:00:00.000Z");
  const [c2Id, c2Fn] = Array.from(scheduled.entries())[0];
  scheduled.delete(c2Id);
  await c2Fn();
  assert.equal(conflicts.length, 2);
  assert.equal(coord.getServerTimestamp("0420010000001"), "");
});

test("UT-P1-SyncCoord-ChainedCampusPromotion: preserves root replace_parcel_id across multi-hop <650ms CAMPUS renames and blocks pre-promotion resurrection", async () => {
  const scheduled = new Map();
  let nextTimerId = 1;
  const rpcLog = [];
  const successEvents = [];
  let deferredSingleRpcResolve = null;

  const coord = createParcelSyncCoordinator({
    debounceMs: 650,
    scheduleTimer: (fn) => {
      const id = nextTimerId++;
      scheduled.set(id, fn);
      return id;
    },
    cancelTimer: (id) => scheduled.delete(id),
    sendDecisionRpc: async (args) => {
      rpcLog.push(args);
      if (args.parcelId === "0420010000009" && !deferredSingleRpcResolve) {
        return await new Promise((resolve) => {
          deferredSingleRpcResolve = resolve;
        });
      }
      return {
        ok: true,
        parcel: { parcel_id: args.parcelId, ...args.patch },
        decision: { parcel_id: args.parcelId, ...args.patch, decided_at: "2026-11-23T16:10:00.000Z" },
      };
    },
    onSuccess: (resp, meta) => successEvents.push({ resp, meta }),
  });

  // 1. Multi-hop <650ms campus rename: 0420010000001 -> CAMPUS:PARISH_LOT -> CAMPUS:PARISH_ASSEMBLAGE
  coord.recordHydratedTimestamp("0420010000001", "2026-11-23T16:00:00.000Z", { fromCloud: true });
  const base1 = coord.promoteParcelKey("0420010000001", "CAMPUS:PARISH_LOT", "2026-11-23T16:00:00.000Z");
  coord.enqueue(
    "CAMPUS:PARISH_LOT",
    {
      parcel_id: "CAMPUS:PARISH_LOT",
      replace_parcel_id: "0420010000001",
      campus_group_id: "Parish Lot",
      significance_level: "high",
    },
    base1
  );
  const base2 = coord.promoteParcelKey("CAMPUS:PARISH_LOT", "CAMPUS:PARISH_ASSEMBLAGE", base1);
  coord.enqueue(
    "CAMPUS:PARISH_ASSEMBLAGE",
    {
      parcel_id: "CAMPUS:PARISH_ASSEMBLAGE",
      replace_parcel_id: "CAMPUS:PARISH_LOT",
      campus_group_id: "Parish Assemblage",
      screening_notes: "Corrected campus name",
    },
    base2
  );
  assert.equal(coord.getPromotedTarget("0420010000001"), "CAMPUS:PARISH_ASSEMBLAGE");

  // Background cloud hydration BEFORE flush must NOT resurrect pre-promotion single-parcel 0420010000001!
  const parcelsById = new Map([
    [
      "CAMPUS:PARISH_ASSEMBLAGE",
      normalizeParcelRecord({
        parcel_id: "CAMPUS:PARISH_ASSEMBLAGE",
        campus_group_id: "Parish Assemblage",
        hcad_num: "0420010000001",
        related_hcads: "0420010000001; 0420010000002",
        significance_level: "high",
      }),
    ],
  ]);
  const decisionsByParcelId = new Map([
    ["CAMPUS:PARISH_ASSEMBLAGE", { parcel_id: "CAMPUS:PARISH_ASSEMBLAGE", screening_notes: "Corrected campus name" }],
  ]);
  hydrateMonitoringParcelAndDecisionMaps({
    payload: {
      parcels: [
        { parcel_id: "0420010000001", hcad_num: "0420010000001", significance_level: "moderate" },
      ],
      decisions: [
        { parcel_id: "0420010000001", hcad_num: "0420010000001", decided_at: "2026-11-23T16:00:00.000Z" },
      ],
    },
    areasById: new Map(),
    parcelsById,
    decisionsByParcelId,
    syncCoordinator: coord,
    fromCloud: true,
  });
  assert.equal(parcelsById.has("0420010000001"), false, "Pre-promotion single parcel must not resurrect during pending CAMPUS promotion");
  assert.equal(parcelsById.has("CAMPUS:PARISH_ASSEMBLAGE"), true);
  assert.equal(decisionsByParcelId.has("0420010000001"), false);

  const [mId, mFn] = Array.from(scheduled.entries())[0];
  scheduled.delete(mId);
  await mFn();
  assert.equal(rpcLog[0].parcelId, "CAMPUS:PARISH_ASSEMBLAGE");
  assert.equal(rpcLog[0].expectedUpdatedAt, "2026-11-23T16:00:00.000Z");
  assert.equal(rpcLog[0].patch.replace_parcel_id, "0420010000001", "Original server PK must survive multi-hop CAMPUS rename");

  // 2. Multi-hop promotion while single-parcel RPC #1 is already in-flight clears all intermediate inFlightPromises and breaks rename-back cycles!
  coord.enqueue("0420010000009", { screening_notes: "First single edit" }, "");
  const [inId, inFn] = Array.from(scheduled.entries())[0];
  scheduled.delete(inId);
  const inFlightP = inFn();
  coord.promoteParcelKey("0420010000009", "CAMPUS:SECOND_LOT", "");
  coord.promoteParcelKey("CAMPUS:SECOND_LOT", "CAMPUS:SECOND_CAMPUS", "");
  coord.enqueue(
    "CAMPUS:SECOND_CAMPUS",
    { parcel_id: "CAMPUS:SECOND_CAMPUS", replace_parcel_id: "0420010000009" },
    ""
  );
  const [cId, cFn] = Array.from(scheduled.entries())[0];
  scheduled.delete(cId);
  const campusP = cFn();
  deferredSingleRpcResolve({
    ok: true,
    parcel: { parcel_id: "0420010000009" },
    decision: { parcel_id: "0420010000009", decided_at: "2026-11-23T16:09:00.000Z" },
  });
  await inFlightP;
  await campusP;
  const supersededEvent = successEvents.find((e) => e.resp.parcel.parcel_id === "0420010000009");
  assert.equal(supersededEvent.meta.hasNewerPendingEdit, true);
  assert.equal(supersededEvent.meta.supersededByParcelId, "CAMPUS:SECOND_CAMPUS");
  assert.equal(coord.hasPendingOrInFlight("CAMPUS:SECOND_LOT"), false);
  assert.equal(
    coord.hasPendingOrInFlight("CAMPUS:SECOND_CAMPUS"),
    false,
    "Intermediate multi-hop campus record must not leak inFlightPromise"
  );

  // Acyclic rename-back: CAMPUS:SECOND_CAMPUS -> CAMPUS:SECOND_LOT -> CAMPUS:SECOND_CAMPUS omits replace_parcel_id
  coord.enqueue("CAMPUS:SECOND_CAMPUS", { parcel_id: "CAMPUS:SECOND_CAMPUS", screening_notes: "Start" }, "2026-11-23T16:10:00.000Z");
  coord.promoteParcelKey("CAMPUS:SECOND_CAMPUS", "CAMPUS:SECOND_LOT", "2026-11-23T16:10:00.000Z");
  coord.enqueue(
    "CAMPUS:SECOND_LOT",
    { parcel_id: "CAMPUS:SECOND_LOT", replace_parcel_id: "CAMPUS:SECOND_CAMPUS" },
    "2026-11-23T16:10:00.000Z"
  );
  coord.promoteParcelKey("CAMPUS:SECOND_LOT", "CAMPUS:SECOND_CAMPUS", "2026-11-23T16:10:00.000Z");
  coord.enqueue(
    "CAMPUS:SECOND_CAMPUS",
    { parcel_id: "CAMPUS:SECOND_CAMPUS", replace_parcel_id: "CAMPUS:SECOND_LOT", screening_notes: "Renamed back" },
    "2026-11-23T16:10:00.000Z"
  );
  assert.equal(coord.getPromotedTarget("CAMPUS:SECOND_LOT"), "CAMPUS:SECOND_CAMPUS");
  assert.equal(coord.getPromotedTarget("CAMPUS:SECOND_CAMPUS"), "CAMPUS:SECOND_CAMPUS");
  const [rbId, rbFn] = Array.from(scheduled.entries())[0];
  scheduled.delete(rbId);
  await rbFn();
  const rbRpc = rpcLog[rpcLog.length - 1];
  assert.equal(rbRpc.parcelId, "CAMPUS:SECOND_CAMPUS");
  assert.equal("replace_parcel_id" in rbRpc.patch, false, "A -> B -> A rename-back within <650ms must omit replace_parcel_id");
});

test("UT-P1-SyncCoord-ReplayAndAncestorSerialization: serializes concurrent timers behind both same-parcel and ancestor CAMPUS replays", async () => {
  const scheduled = new Map();
  let nextTimerId = 1;
  const rpcLog = [];
  let deferredAncestorReplay = null;
  const responses = [
    ({ retryFn }) => ({ retryFn, resp: { ok: false, tokenExpired: true, queuedForRetry: true } }),
    () => ({
      promise: new Promise((resolve) => {
        deferredAncestorReplay = resolve;
      }),
    }),
    ({ parcelId, patch }) => ({
      resp: {
        ok: true,
        parcel: { parcel_id: parcelId, ...patch },
        decision: { parcel_id: parcelId, ...patch, decided_at: "2026-11-23T16:20:00.000Z" },
      },
    }),
  ];
  let queuedAncestorRetry = null;

  const coord = createParcelSyncCoordinator({
    debounceMs: 650,
    scheduleTimer: (fn) => {
      const id = nextTimerId++;
      scheduled.set(id, fn);
      return id;
    },
    cancelTimer: (id) => scheduled.delete(id),
    sendDecisionRpc: async ({ parcelId, expectedUpdatedAt, patch, retryFn }) => {
      rpcLog.push({ parcelId, expectedUpdatedAt, patch });
      const step = responses.shift()({ parcelId, expectedUpdatedAt, patch, retryFn });
      if (step.retryFn) queuedAncestorRetry = step.retryFn;
      return step.promise ? await step.promise : step.resp;
    },
  });

  // Queue expired-token retry on 0420010000001, then promote 0420010000001 -> CAMPUS:PARISH
  coord.enqueue("0420010000001", { screening_notes: "Expired single edit" }, "2026-11-23T16:00:00.000Z");
  const [t1Id, t1Fn] = Array.from(scheduled.entries())[0];
  scheduled.delete(t1Id);
  await t1Fn();
  const baseTs = coord.promoteParcelKey("0420010000001", "CAMPUS:PARISH", "2026-11-23T16:00:00.000Z");
  coord.enqueue(
    "CAMPUS:PARISH",
    { parcel_id: "CAMPUS:PARISH", replace_parcel_id: "0420010000001", screening_notes: "Promoted campus" },
    baseTs
  );

  // Start replaying ancestor retry, then fire CAMPUS:PARISH debounce timer while replay is mid-flight!
  const replayPromise = queuedAncestorRetry();
  const [t2Id, t2Fn] = Array.from(scheduled.entries())[0];
  scheduled.delete(t2Id);
  const campusTimerPromise = t2Fn();
  assert.equal(rpcLog.length, 2, "Promoted CAMPUS timer must await active ancestor replay before dispatching");
  deferredAncestorReplay({
    ok: true,
    parcel: { parcel_id: "0420010000001" },
    decision: { parcel_id: "0420010000001", decided_at: "2026-11-23T16:15:00.000Z" },
  });
  await replayPromise;
  await campusTimerPromise;
  assert.equal(rpcLog.length, 3);
  assert.equal(rpcLog[2].parcelId, "CAMPUS:PARISH");
  assert.equal(rpcLog[2].expectedUpdatedAt, "2026-11-23T16:15:00.000Z");
});

test("UT-P1-SyncCoord-ClearAllSignOutCancellation: clearAll cancels pending timers and invalidates pre-sign-out retry closures", async () => {
  const scheduled = new Map();
  let nextTimerId = 1;
  let capturedRetry = null;

  const coord = createParcelSyncCoordinator({
    debounceMs: 650,
    scheduleTimer: (fn) => {
      const id = nextTimerId++;
      scheduled.set(id, fn);
      return id;
    },
    cancelTimer: (id) => scheduled.delete(id),
    sendDecisionRpc: async ({ retryFn }) => {
      capturedRetry = retryFn;
      return { ok: false, tokenExpired: true, queuedForRetry: true };
    },
  });

  coord.enqueue("CAMPUS:PARISH_ASSEMBLAGE", { screening_notes: "Before sign-out" }, "2026-11-23T16:05:00.000Z");
  const [qId, qFn] = Array.from(scheduled.entries())[0];
  scheduled.delete(qId);
  await qFn();
  assert.equal(coord.hasPendingOrInFlight("CAMPUS:PARISH_ASSEMBLAGE"), true);
  coord.enqueue("0420090000099", { screening_notes: "Pending timer cancelled" }, "");
  assert.equal(scheduled.size, 1);

  coord.clearAll();
  assert.equal(scheduled.size, 0);
  assert.equal(coord.hasPendingOrInFlight("CAMPUS:PARISH_ASSEMBLAGE"), false);
  assert.equal((await capturedRetry()).cancelled, true);
});

test("UT-P1-SyncCoord-MultiParcelQueuedRetryFifoAndEviction: preserves FIFO order across multi-parcel replayPendingAuthRetries and releases tickets on retry eviction", async () => {
  clearPendingAuthRetries();
  try {
    const scheduled = new Map();
    let nextTimerId = 1;
    const dispatchedCalls = [];
    const successCalls = [];
    let deferredParcelAResolve = null;
    const nowSec = Math.floor(Date.now() / 1000);

    let currentSession = {
      authorized: true,
      email: "staff.a@preservation.example.org",
      role: "staff",
      googleIdToken: makeSyntheticJwt({
        email: "staff.a@preservation.example.org",
        exp: nowSec - 120,
      }),
    };

    const coord = createParcelSyncCoordinator({
      debounceMs: 650,
      scheduleTimer: (fn) => {
        const id = nextTimerId++;
        scheduled.set(id, fn);
        return id;
      },
      cancelTimer: (id) => scheduled.delete(id),
      sendDecisionRpc: async ({ parcelId, expectedUpdatedAt, patch, retryFn }) =>
        await callMonitoringRpc({
          webhookUrl: "https://example.org/exec",
          action: "upsert_parcel_decision",
          body: { parcel_id: parcelId, expected_updated_at: expectedUpdatedAt, ...patch },
          session: currentSession,
          retryFn,
          fetchImpl: async (_url, init) => {
            const parsed = JSON.parse(init.body);
            dispatchedCalls.push(parsed);
            if (parsed.parcel_id === "0420010000001") {
              const payload = await new Promise((resolve) => {
                deferredParcelAResolve = resolve;
              });
              return { ok: true, json: async () => payload };
            }
            const nextTs = `2026-11-23T17:00:0${dispatchedCalls.length}.000Z`;
            return {
              ok: true,
              json: async () => ({
                ok: true,
                parcel: { parcel_id: parsed.parcel_id },
                decision: { parcel_id: parsed.parcel_id, decided_at: nextTs },
              }),
            };
          },
        }),
      onSuccess: (resp, meta) => successCalls.push({ resp, meta }),
    });

    // 1. Both Parcel A and Parcel B expire, queuing [retry_A1 (seq=1), retry_B1 (seq=2)]
    coord.enqueue("0420010000001", { screening_notes: "A1" }, "2026-11-23T16:50:00.000Z");
    const [a1Id, a1Fn] = Array.from(scheduled.entries())[0];
    scheduled.delete(a1Id);
    await a1Fn();

    coord.enqueue("0420010000002", { screening_notes: "B1" }, "2026-11-23T16:55:00.000Z");
    const [b1Id, b1Fn] = Array.from(scheduled.entries())[0];
    scheduled.delete(b1Id);
    await b1Fn();
    assert.equal(getPendingAuthRetryCount(), 2);
    assert.equal(coord.hasPendingOrInFlight("0420010000002"), true);

    // 2. Re-authenticate and begin replayPendingAuthRetries(); while retry_A1 is mid-flight,
    // promote 0420010000002 -> CAMPUS:DUPLEX_LOTS and fire its debounce timer (seq=3)!
    currentSession = {
      authorized: true,
      email: "staff.a@preservation.example.org",
      role: "staff",
      googleIdToken: makeSyntheticJwt({
        email: "staff.a@preservation.example.org",
        exp: nowSec + 3600,
      }),
    };
    const replayPromise = replayPendingAuthRetries({ session: currentSession });
    assert.equal(dispatchedCalls.length, 1);
    assert.equal(dispatchedCalls[0].parcel_id, "0420010000001");

    const baseCampusTs = coord.promoteParcelKey(
      "0420010000002",
      "CAMPUS:DUPLEX_LOTS",
      "2026-11-23T16:55:00.000Z"
    );
    coord.enqueue(
      "CAMPUS:DUPLEX_LOTS",
      {
        parcel_id: "CAMPUS:DUPLEX_LOTS",
        replace_parcel_id: "0420010000002",
        screening_notes: "B2 promoted",
      },
      baseCampusTs
    );
    const [b2Id, b2Fn] = Array.from(scheduled.entries())[0];
    scheduled.delete(b2Id);
    const b2TimerPromise = b2Fn();
    assert.equal(
      dispatchedCalls.length,
      1,
      "Promoted CAMPUS edit (seq=3) must wait behind unreplayed retry_B1 (seq=2)"
    );

    deferredParcelAResolve({
      ok: true,
      parcel: { parcel_id: "0420010000001" },
      decision: { parcel_id: "0420010000001", decided_at: "2026-11-23T17:00:01.000Z" },
    });
    await replayPromise;
    await b2TimerPromise;

    assert.deepEqual(
      dispatchedCalls.map((c) => c.parcel_id),
      ["0420010000001", "0420010000002", "CAMPUS:DUPLEX_LOTS"]
    );
    assert.equal(dispatchedCalls[1].expected_updated_at, "2026-11-23T16:55:00.000Z");
    assert.equal(
      dispatchedCalls[2].expected_updated_at,
      "2026-11-23T17:00:02.000Z",
      "CAMPUS:DUPLEX_LOTS must chain onto retry_B1 server timestamp"
    );
    const b1Success = successCalls.find((s) => s.resp.decision.parcel_id === "0420010000002");
    const b2Success = successCalls.find((s) => s.resp.decision.parcel_id === "CAMPUS:DUPLEX_LOTS");
    assert.equal(b1Success.meta.hasNewerPendingEdit, true);
    assert.equal(b2Success.meta.hasNewerPendingEdit, false);
    assert.equal(coord.hasPendingOrInFlight("0420010000002"), false);
    assert.equal(coord.hasPendingOrInFlight("CAMPUS:DUPLEX_LOTS"), false);

    // 3. Evicting a queued retry without replaying it releases activeBatchTickets immediately
    currentSession = {
      authorized: true,
      email: "staff.a@preservation.example.org",
      role: "staff",
      googleIdToken: makeSyntheticJwt({
        email: "staff.a@preservation.example.org",
        exp: nowSec - 120,
      }),
    };
    coord.enqueue("0420010000009", { screening_notes: "Will be evicted" }, "");
    const [evId, evFn] = Array.from(scheduled.entries())[0];
    scheduled.delete(evId);
    await evFn();
    assert.equal(coord.hasPendingOrInFlight("0420010000009"), true);
    clearPendingAuthRetries();
    assert.equal(
      coord.hasPendingOrInFlight("0420010000009"),
      false,
      "Evicting queued retry must release coordinator batch ticket"
    );
  } finally {
    clearPendingAuthRetries();
  }
});

test("UT-P1-AuthClient-StorePrecedenceAndRetryIsolation: pullCloudMonitoringState honors sessionStore precedence, isolates explicit stores from ambient localStorage/sessionStorage, and preserves surviving owner retries", async () => {
  clearPendingAuthRetries();
  const hadAmbientLocal = Object.prototype.hasOwnProperty.call(globalThis, "localStorage");
  const prevAmbientLocal = globalThis.localStorage;
  const hadAmbientSession = Object.prototype.hasOwnProperty.call(globalThis, "sessionStorage");
  const prevAmbientSession = globalThis.sessionStorage;
  const validExpSec = Math.floor(Date.now() / 1000) + 3600;

  const ambientLocalStore = createMemoryStorage({
    [LS_ADMIN_SESSION_KEY]: JSON.stringify({
      authorized: true,
      email: "ambient.local@preservation.example.org",
      role: "staff",
      googleIdToken: makeSyntheticJwt({
        email: "ambient.local@preservation.example.org",
        exp: validExpSec,
      }),
    }),
    [`${LS_AUTH_CACHE_PREFIX}staff@preservation.example.org`]: '{"role":"staff","parcels":[{"parcel_id":"0152300000001"}]}',
    [LS_CONFIGURED_GOOGLE_CLIENT_ID_KEY]: "ambient-client-id.apps.googleusercontent.com",
    "phpam:watchlist:v2": '{"legacy":true}',
  });
  const ambientSessionStore = createMemoryStorage({
    [LS_ADMIN_SESSION_KEY]: JSON.stringify({
      authorized: true,
      email: "ambient.session@preservation.example.org",
      role: "staff",
      googleIdToken: makeSyntheticJwt({
        email: "ambient.session@preservation.example.org",
        exp: validExpSec,
      }),
    }),
  });
  Object.defineProperty(globalThis, "localStorage", {
    value: ambientLocalStore,
    configurable: true,
    writable: true,
  });
  Object.defineProperty(globalThis, "sessionStorage", {
    value: ambientSessionStore,
    configurable: true,
    writable: true,
  });

  try {
    // 1. Explicit { sessionStore } / { localStore: undefined } / { sessionStore: undefined } never reads or mutates ambient localStorage or sessionStorage
    const screenerSessionStore = createMemoryStorage({
      [LS_ADMIN_SESSION_KEY]: JSON.stringify({
        authorized: true,
        email: "screener@preservation.example.org",
        role: "screener",
        googleIdToken: makeSyntheticJwt({
          email: "screener@preservation.example.org",
          exp: validExpSec,
        }),
      }),
    });
    const bootSess = ensureCleanUnauthenticatedStorageOnBoot({ sessionStore: screenerSessionStore });
    assert.equal(bootSess.role, "screener");
    assert.equal(
      ambientLocalStore.getItem(`${LS_AUTH_CACHE_PREFIX}staff@preservation.example.org`),
      '{"role":"staff","parcels":[{"parcel_id":"0152300000001"}]}',
      "ensureCleanUnauthenticatedStorageOnBoot({ sessionStore }) must not purge ambient localStorage"
    );
    assert.equal(ambientLocalStore.getItem("phpam:watchlist:v2"), '{"legacy":true}');

    const unauthTabSessionStore = createMemoryStorage();
    assert.equal(
      ensureCleanUnauthenticatedStorageOnBoot({
        localStore: undefined,
        sessionStore: unauthTabSessionStore,
      }),
      null
    );
    purgePrivateMonitoringStorage({ sessionStore: unauthTabSessionStore });
    purgePrivateMonitoringStorage({ localStore: undefined });
    assert.equal(
      saveCachedMonitoringState(
        "staff@preservation.example.org",
        "staff",
        { parcels: [{ parcel_id: "9999999999999" }] },
        { sessionStore: unauthTabSessionStore }
      ),
      false
    );
    assert.equal(
      loadCachedMonitoringState("staff@preservation.example.org", "staff", {
        sessionStore: unauthTabSessionStore,
      }),
      null
    );
    assert.equal(
      resolveConfiguredGoogleClientId({ sessionStore: unauthTabSessionStore }),
      ""
    );
    assert.equal(
      readActiveMonitoringSession({ localStore: createMemoryStorage(), sessionStore: undefined }),
      null,
      "Explicit { localStore, sessionStore: undefined } must not read ambient sessionStorage"
    );
    let isolatedOwnerQueued = false;
    const frozenRetryFn = Object.freeze(async () => {
      isolatedOwnerQueued = true;
      return { ok: true };
    });
    queuePendingAuthRetry(frozenRetryFn, "session_scoped_frozen_retry", null, {
      sessionStore: screenerSessionStore,
    });
    assert.equal(
      (await replayPendingAuthRetries({ sessionStore: unauthTabSessionStore })).length,
      0,
      "queuePendingAuthRetry(..., opts) must bind owner strictly from opts.sessionStore and support frozen functions"
    );
    assert.equal(isolatedOwnerQueued, false);
    queuePendingAuthRetry(frozenRetryFn, "session_scoped_frozen_retry_2", null, {
      sessionStore: screenerSessionStore,
    });
    assert.equal(
      (await replayPendingAuthRetries({ sessionStore: screenerSessionStore })).length,
      1
    );
    assert.equal(isolatedOwnerQueued, true);
    const unauthRpc = await callMonitoringRpc({
      webhookUrl: "https://example.org/exec",
      action: "pull_monitoring_state",
      sessionStore: unauthTabSessionStore,
    });
    assert.equal(unauthRpc.authorized, false, "callMonitoringRpc({ sessionStore }) must not fall back to ambient stores");
    const sessionOnlyRpcEmails = [];
    const screenerOnlyRpc = await callMonitoringRpc({
      webhookUrl: "https://example.org/exec",
      action: "pull_monitoring_state",
      sessionStore: screenerSessionStore,
      fetchImpl: async (_url, init) => {
        sessionOnlyRpcEmails.push(JSON.parse(init.body).admin_email);
        return { ok: true, json: async () => ({ ok: true, authorized: true, role: "screener" }) };
      },
    });
    assert.equal(screenerOnlyRpc.ok, true);
    assert.deepEqual(sessionOnlyRpcEmails, ["screener@preservation.example.org"]);

    signOutMonitoringSession({ localStore: undefined, sessionStore: unauthTabSessionStore });
    assert.equal(
      ambientLocalStore.getItem(`${LS_AUTH_CACHE_PREFIX}staff@preservation.example.org`),
      '{"role":"staff","parcels":[{"parcel_id":"0152300000001"}]}',
      "Explicit { localStore: undefined, sessionStore } must normalize localStore to null"
    );
    assert.equal(ambientLocalStore.getItem("phpam:watchlist:v2"), '{"legacy":true}');

    // 2. pullCloudMonitoringState honors sessionStorage-only session AND tab-scoped screener precedence over localStorage staff
    const staffLocalStore = createMemoryStorage({
      [LS_ADMIN_SESSION_KEY]: JSON.stringify({
        authorized: true,
        email: "staff@preservation.example.org",
        role: "staff",
        googleIdToken: makeSyntheticJwt({
          email: "staff@preservation.example.org",
          exp: validExpSec,
        }),
      }),
    });
    const sentEmails = [];
    const pullResp = await pullCloudMonitoringState({
      webhookUrl: "https://example.org/exec",
      localStore: staffLocalStore,
      sessionStore: screenerSessionStore,
      fetchImpl: async (_url, init) => {
        const parsed = JSON.parse(init.body);
        sentEmails.push(parsed.admin_email);
        return {
          ok: true,
          json: async () => ({ ok: true, authorized: true, role: "screener", parcels: [], decisions: [] }),
        };
      },
    });
    assert.equal(pullResp.ok, true);
    assert.equal(
      sentEmails[0],
      "screener@preservation.example.org",
      "sessionStore screener must take precedence over localStore staff"
    );

    // 3. Cross-user / sessionStore / cross-tab isolation + throwing cancelQueuedRetry isolation
    let userARetryExecuted = false;
    let userARetryCancelled = false;
    const throwingCancelFn = async () => ({ ok: true });
    throwingCancelFn.cancelQueuedRetry = () => {
      throw new Error("Simulated cancel hook failure");
    };
    const userARetryFn = async () => {
      userARetryExecuted = true;
      return { ok: true };
    };
    userARetryFn.cancelQueuedRetry = () => {
      userARetryCancelled = true;
    };
    queuePendingAuthRetry(throwingCancelFn, "throwing_cancel_op", {
      email: "staff.a@preservation.example.org",
      role: "staff",
      authorized: true,
    });
    queuePendingAuthRetry(userARetryFn, "upsert_watchlist_dossier", {
      email: "staff.a@preservation.example.org",
      role: "staff",
      authorized: true,
    });
    assert.equal(getPendingAuthRetryCount(), 2);

    const userBSessionStore = createMemoryStorage({
      [LS_ADMIN_SESSION_KEY]: JSON.stringify({
        authorized: true,
        email: "staff.b@preservation.example.org",
        role: "staff",
      }),
    });
    const replayRes = await replayPendingAuthRetries({ sessionStore: userBSessionStore });
    assert.equal(replayRes.length, 0);
    assert.equal(userARetryExecuted, false);
    assert.equal(
      userARetryCancelled,
      true,
      "Throwing cancelQueuedRetry on item 1 must not prevent item 2 cancellation"
    );
    assert.equal(getPendingAuthRetryCount(), 0);

    const executedOps = [];
    let switchedOutCancelled = false;
    const switchUserAFn = async () => {
      executedOps.push("user_a_op");
      return { ok: true };
    };
    switchUserAFn.cancelQueuedRetry = () => {
      switchedOutCancelled = true;
    };
    queuePendingAuthRetry(throwingCancelFn, "throwing_switch_op", {
      email: "staff.a@preservation.example.org",
      role: "staff",
      authorized: true,
    });
    queuePendingAuthRetry(switchUserAFn, "user_a_op", {
      email: "staff.a@preservation.example.org",
      role: "staff",
      authorized: true,
    });
    queuePendingAuthRetry(
      async () => {
        executedOps.push("user_b_op");
        return { ok: true };
      },
      "user_b_op",
      {
        email: "staff.b@preservation.example.org",
        role: "staff",
        authorized: true,
      }
    );
    assert.equal(getPendingAuthRetryCount(), 3);

    const userBLocalStore = createMemoryStorage({
      [LS_ADMIN_SESSION_KEY]: JSON.stringify({
        authorized: true,
        email: "staff.b@preservation.example.org",
        role: "staff",
      }),
    });
    let listener = null;
    const unsubSwitch = subscribeCrossTabAuthChanges(
      () => {},
      {
        addEventListener: (_type, fn) => {
          listener = fn;
        },
        removeEventListener: () => {},
      },
      { localStore: userBLocalStore }
    );
    listener({ key: LS_ADMIN_SESSION_KEY });
    unsubSwitch();
    assert.equal(getPendingAuthRetryCount(), 1);
    assert.equal(switchedOutCancelled, true);
    await replayPendingAuthRetries({ localStore: userBLocalStore });
    assert.deepEqual(executedOps, ["user_b_op"], "Only User B's retry must survive cross-tab switch");

    queuePendingAuthRetry(throwingCancelFn, "user_b_throwing_signout_op", {
      email: "staff.b@preservation.example.org",
      role: "staff",
      authorized: true,
    });
    let secondSignOutCancelled = false;
    const secondSignOutFn = async () => ({ ok: true });
    secondSignOutFn.cancelQueuedRetry = () => {
      secondSignOutCancelled = true;
    };
    queuePendingAuthRetry(secondSignOutFn, "user_b_signout_op", {
      email: "staff.b@preservation.example.org",
      role: "staff",
      authorized: true,
    });
    const emptyStore = createMemoryStorage();
    const unsubOut = subscribeCrossTabAuthChanges(
      () => {},
      {
        addEventListener: (_type, fn) => {
          listener = fn;
        },
        removeEventListener: () => {},
      },
      { localStore: emptyStore }
    );
    listener({ key: LS_ADMIN_SESSION_KEY });
    unsubOut();
    assert.equal(secondSignOutCancelled, true);
    assert.equal(getPendingAuthRetryCount(), 0);
  } finally {
    clearPendingAuthRetries();
    if (hadAmbientLocal) {
      Object.defineProperty(globalThis, "localStorage", {
        value: prevAmbientLocal,
        configurable: true,
        writable: true,
      });
    } else {
      delete globalThis.localStorage;
    }
    if (hadAmbientSession) {
      Object.defineProperty(globalThis, "sessionStorage", {
        value: prevAmbientSession,
        configurable: true,
        writable: true,
      });
    } else {
      delete globalThis.sessionStorage;
    }
  }
});

test("UT-P1-SyncCoord-ClearRetriesCascadesToBlockedBatches: cancels queued, ticket-blocked, and debounced batches on clearPendingAuthRetries", async (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  const timers = createManualTimerQueue();
  const expiredLocalStore = createMemoryStorage({
    [LS_ADMIN_SESSION_KEY]: makeSerializedSession({
      email: "staff.a@preservation.example.org",
      role: "staff",
      expOffsetSec: -120,
    }),
  });
  const coord = createParcelSyncCoordinator({
    debounceMs: 650,
    scheduleTimer: timers.scheduleTimer,
    cancelTimer: timers.cancelTimer,
    sendDecisionRpc: async ({ parcelId, expectedUpdatedAt, patch, retryFn }) =>
      await callMonitoringRpc({
        webhookUrl: "https://example.org/exec",
        action: "upsert_parcel_decision",
        body: { parcel_id: parcelId, expected_updated_at: expectedUpdatedAt, ...patch },
        localStore: expiredLocalStore,
        retryFn,
        fetchImpl: async () => ({ ok: true, json: async () => ({ ok: true }) }),
      }),
  });

  coord.enqueue("0420090000088", { screening_notes: "Batch 1" }, "");
  await timers.popNextTimer()();
  coord.enqueue("0420090000088", { screening_notes: "Batch 2 blocked" }, "");
  const blockedBatch2Promise = timers.popNextTimer()();
  coord.enqueue("0420090000088", { screening_notes: "Batch 3 debounced" }, "");

  clearPendingAuthRetries();
  const cancelledBatch2 = await blockedBatch2Promise;

  assert.equal(coord.hasPendingOrInFlight("0420090000088"), false);
  assert.equal(timers.scheduled.size, 0);
  assert.equal(cancelledBatch2.cancelled, true);
  assert.equal(getPendingAuthRetryCount(), 0);
});

test("UT-P1-SyncCoord-AccountSwitchRollsBackUncommittedPromotion: rolls back uncommitted CAMPUS promotion when shared browser switches users", async (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  const timers = createManualTimerQueue();
  const sentPayloads = [];
  const successMeta = [];
  const nowSec = Math.floor(Date.now() / 1000);
  const sharedLocalStore = createMemoryStorage({
    [LS_ADMIN_SESSION_KEY]: makeSerializedSession({
      email: "staff.a@preservation.example.org",
      role: "staff",
      expOffsetSec: -120,
    }),
  });
  const sharedSessionStore = createMemoryStorage();
  const coord = createParcelSyncCoordinator({
    debounceMs: 650,
    scheduleTimer: timers.scheduleTimer,
    cancelTimer: timers.cancelTimer,
    onSuccess: (_resp, meta) => successMeta.push(meta),
    sendDecisionRpc: async ({ parcelId, expectedUpdatedAt, patch, retryFn }) =>
      await callMonitoringRpc({
        webhookUrl: "https://example.org/exec",
        action: "upsert_parcel_decision",
        body: { parcel_id: parcelId, expected_updated_at: expectedUpdatedAt, ...patch },
        localStore: sharedLocalStore,
        sessionStore: sharedSessionStore,
        retryFn,
        fetchImpl: async (_url, init) => {
          const parsed = JSON.parse(init.body);
          sentPayloads.push(parsed);
          return {
            ok: true,
            json: async () => ({
              ok: true,
              parcel: { parcel_id: parsed.parcel_id },
              decision: { parcel_id: parsed.parcel_id, decided_at: "2026-11-23T18:00:00.000Z" },
            }),
          };
        },
      }),
  });

  coord.enqueue("0420090000089", { screening_notes: "User A pre-switch 1" }, "");
  await timers.popNextTimer()();
  coord.promoteParcelKey("0420090000089", "CAMPUS:PARISH", "");
  coord.enqueue(
    "CAMPUS:PARISH",
    { parcel_id: "CAMPUS:PARISH", replace_parcel_id: "0420090000089", screening_notes: "User A pre-switch 2" },
    ""
  );
  const blockedSwitchPromise = timers.popNextTimer()();

  const signInUserB = await verifyAndSignInMonitoring({
    webhookUrl: "https://example.org/exec",
    googleIdToken: makeSyntheticJwt({
      email: "staff.b@preservation.example.org",
      exp: nowSec + 3600,
    }),
    localStore: sharedLocalStore,
    sessionStore: sharedSessionStore,
    fetchImpl: async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        ok: true,
        authorized: true,
        email: "staff.b@preservation.example.org",
        role: "staff",
        supportsRoamingSync: false,
        supportsPhase1Monitoring: true,
      }),
    }),
  });
  const blockedRes = await blockedSwitchPromise;
  coord.enqueue("0420090000089", { screening_notes: "User B fresh single-parcel save" }, "");
  const ubRes = await timers.popNextTimer()();

  assert.equal(signInUserB.ok, true);
  assert.equal(signInUserB.switchedAccountOrRole, true);
  assert.equal(blockedRes.cancelled, true);
  assert.equal(sentPayloads.length, 1);
  assert.equal(sentPayloads[0].admin_email, "staff.b@preservation.example.org");
  assert.equal(coord.getPromotedTarget("0420090000089"), "0420090000089");
  assert.equal(ubRes.ok, true);
  assert.deepEqual(successMeta[0], {
    hasNewerPendingEdit: false,
    supersededByParcelId: "",
  });
});

test("UT-P1-SyncCoord-MultiHopEvictionCancelsIntermediateTicket: cancels intermediate blocked tickets across multi-hop uncommitted promotions on single-item eviction", async (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  const timers = createManualTimerQueue();
  const sentPayloads = [];
  const nowSec = Math.floor(Date.now() / 1000);
  const expiredStaffSession = {
    authorized: true,
    email: "staff.a@preservation.example.org",
    role: "staff",
    googleIdToken: makeSyntheticJwt({
      email: "staff.a@preservation.example.org",
      exp: nowSec - 120,
    }),
  };
  const coord = createParcelSyncCoordinator({
    debounceMs: 650,
    scheduleTimer: timers.scheduleTimer,
    cancelTimer: timers.cancelTimer,
    sendDecisionRpc: async ({ parcelId, expectedUpdatedAt, patch, retryFn }) =>
      await callMonitoringRpc({
        webhookUrl: "https://example.org/exec",
        action: "upsert_parcel_decision",
        body: { parcel_id: parcelId, expected_updated_at: expectedUpdatedAt, ...patch },
        session: expiredStaffSession,
        localStore: null,
        retryFn,
        fetchImpl: async (_url, init) => {
          sentPayloads.push(JSON.parse(init.body));
          return { ok: true, json: async () => ({ ok: true }) };
        },
      }),
  });

  coord.enqueue("0420090000091", { screening_notes: "Hop 1 root" }, "");
  await timers.popNextTimer()();
  coord.promoteParcelKey("0420090000091", "CAMPUS:INTERMEDIATE_LOT", "");
  coord.enqueue("CAMPUS:INTERMEDIATE_LOT", { screening_notes: "Hop 2 intermediate" }, "");
  const intermediateTicketPromise = timers.popNextTimer()();
  coord.promoteParcelKey("CAMPUS:INTERMEDIATE_LOT", "CAMPUS:FINAL_PARISH", "");

  await replayPendingAuthRetries({
    session: { email: "screener@preservation.example.org", role: "screener", authorized: true },
    localStore: null,
  });
  const intermediateResult = await intermediateTicketPromise;

  assert.equal(intermediateResult.cancelled, true);
  assert.equal(sentPayloads.length, 0);
  assert.equal(coord.hasPendingOrInFlight("0420090000091"), false);
  assert.equal(coord.hasPendingOrInFlight("CAMPUS:INTERMEDIATE_LOT"), false);
  assert.equal(coord.getPromotedTarget("0420090000091"), "0420090000091");
  assert.equal(coord.getPromotedTarget("CAMPUS:INTERMEDIATE_LOT"), "CAMPUS:INTERMEDIATE_LOT");
});

test("UT-P1-SyncCoord-UpstreamRpcDoesNotCommitDownstreamPromotion: rolls back concurrent uncommitted promotion even after pre-promotion single-parcel RPC resolves", async (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  const timers = createManualTimerQueue();
  const upstreamGate = createDeferred();
  const store = createMemoryStorage({
    [LS_ADMIN_SESSION_KEY]: makeSerializedSession({
      email: "staff.a@preservation.example.org",
      role: "staff",
      expOffsetSec: 3600,
    }),
  });
  const coord = createParcelSyncCoordinator({
    debounceMs: 650,
    scheduleTimer: timers.scheduleTimer,
    cancelTimer: timers.cancelTimer,
    sendDecisionRpc: async ({ parcelId, expectedUpdatedAt, patch, retryFn }) =>
      await callMonitoringRpc({
        webhookUrl: "https://example.org/exec",
        action: "upsert_parcel_decision",
        body: { parcel_id: parcelId, expected_updated_at: expectedUpdatedAt, ...patch },
        localStore: store,
        retryFn,
        fetchImpl: async (_url, init) => {
          const parsed = JSON.parse(init.body);
          await upstreamGate.promise;
          return {
            ok: true,
            json: async () => ({
              ok: true,
              parcel: { parcel_id: parsed.parcel_id },
              decision: { parcel_id: parsed.parcel_id, decided_at: "2026-11-23T18:10:00.000Z" },
            }),
          };
        },
      }),
  });

  coord.enqueue("0420010000009", { screening_notes: "Pre-promotion single parcel" }, "");
  const upstreamPromise = timers.popNextTimer()();
  coord.promoteParcelKey("0420010000009", "CAMPUS:UNCOMMITTED_FIRST", "");
  upstreamGate.resolve();
  await upstreamPromise;

  store.setItem(
    LS_ADMIN_SESSION_KEY,
    makeSerializedSession({
      email: "staff.a@preservation.example.org",
      role: "staff",
      expOffsetSec: -120,
    })
  );
  coord.enqueue("CAMPUS:UNCOMMITTED_FIRST", { screening_notes: "Uncommitted campus" }, "");
  await timers.popNextTimer()();
  await replayPendingAuthRetries({
    session: { email: "screener@preservation.example.org", role: "screener", authorized: true },
    localStore: null,
  });

  assert.equal(coord.getPromotedTarget("0420010000009"), "0420010000009");
});

test("UT-P1-SyncCoord-PrePromotionTargetRpcDoesNotCommitConcurrentMerge: rolls back concurrent parcel-into-campus promotion when pre-existing target RPC resolves first", async (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  const timers = createManualTimerQueue();
  const targetGate = createDeferred();
  const store = createMemoryStorage({
    [LS_ADMIN_SESSION_KEY]: makeSerializedSession({
      email: "staff.a@preservation.example.org",
      role: "staff",
      expOffsetSec: 3600,
    }),
  });
  const coord = createParcelSyncCoordinator({
    debounceMs: 650,
    scheduleTimer: timers.scheduleTimer,
    cancelTimer: timers.cancelTimer,
    sendDecisionRpc: async ({ parcelId, expectedUpdatedAt, patch, retryFn }) =>
      await callMonitoringRpc({
        webhookUrl: "https://example.org/exec",
        action: "upsert_parcel_decision",
        body: { parcel_id: parcelId, expected_updated_at: expectedUpdatedAt, ...patch },
        localStore: store,
        retryFn,
        fetchImpl: async (_url, init) => {
          const parsed = JSON.parse(init.body);
          await targetGate.promise;
          return {
            ok: true,
            json: async () => ({
              ok: true,
              parcel: { parcel_id: parsed.parcel_id },
              decision: { parcel_id: parsed.parcel_id, decided_at: "2026-11-23T18:12:00.000Z" },
            }),
          };
        },
      }),
  });

  coord.enqueue("CAMPUS:ST_THOMAS", { screening_notes: "Pre-existing campus edit" }, "");
  const batch1Promise = timers.popNextTimer()();

  store.setItem(
    LS_ADMIN_SESSION_KEY,
    makeSerializedSession({
      email: "staff.a@preservation.example.org",
      role: "staff",
      expOffsetSec: -120,
    })
  );
  coord.promoteParcelKey("0420010000099", "CAMPUS:ST_THOMAS", "");
  coord.enqueue(
    "CAMPUS:ST_THOMAS",
    { parcel_id: "CAMPUS:ST_THOMAS", replace_parcel_id: "0420010000099", screening_notes: "Merged parcel" },
    ""
  );
  const batch2Promise = timers.popNextTimer()();

  targetGate.resolve();
  await batch1Promise;
  await batch2Promise;
  await replayPendingAuthRetries({
    session: { email: "screener@preservation.example.org", role: "screener", authorized: true },
    localStore: null,
  });

  assert.equal(coord.getPromotedTarget("0420010000099"), "0420010000099");
});

test("UT-P1-SyncCoord-RePromotionEvictionRestoresLastCommittedTarget: restores prior server-committed CAMPUS target when subsequent re-promotion is evicted", async (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  const timers = createManualTimerQueue();
  const store = createMemoryStorage({
    [LS_ADMIN_SESSION_KEY]: makeSerializedSession({
      email: "staff.a@preservation.example.org",
      role: "staff",
      expOffsetSec: 3600,
    }),
  });
  const coord = createParcelSyncCoordinator({
    debounceMs: 650,
    scheduleTimer: timers.scheduleTimer,
    cancelTimer: timers.cancelTimer,
    sendDecisionRpc: async ({ parcelId, expectedUpdatedAt, patch, retryFn }) =>
      await callMonitoringRpc({
        webhookUrl: "https://example.org/exec",
        action: "upsert_parcel_decision",
        body: { parcel_id: parcelId, expected_updated_at: expectedUpdatedAt, ...patch },
        localStore: store,
        retryFn,
        fetchImpl: async (_url, init) => {
          const parsed = JSON.parse(init.body);
          return {
            ok: true,
            json: async () => ({
              ok: true,
              parcel: { parcel_id: parsed.parcel_id },
              decision: { parcel_id: parsed.parcel_id, decided_at: "2026-11-23T18:15:00.000Z" },
            }),
          };
        },
      }),
  });

  coord.promoteParcelKey("0420010000009", "CAMPUS:COMMITTED_B", "");
  coord.enqueue("CAMPUS:COMMITTED_B", { screening_notes: "Committed B" }, "");
  await timers.popNextTimer()();

  store.setItem(
    LS_ADMIN_SESSION_KEY,
    makeSerializedSession({
      email: "staff.a@preservation.example.org",
      role: "staff",
      expOffsetSec: -120,
    })
  );
  coord.promoteParcelKey("0420010000009", "CAMPUS:EVICTED_C", "");
  coord.enqueue("CAMPUS:EVICTED_C", { screening_notes: "Evicted C" }, "");
  await timers.popNextTimer()();
  await replayPendingAuthRetries({
    session: { email: "screener@preservation.example.org", role: "screener", authorized: true },
    localStore: null,
  });

  assert.equal(coord.getPromotedTarget("0420010000009"), "CAMPUS:COMMITTED_B");
});

test("UT-P1-SyncCoord-MultiHopReverseEvictionRestoresFullChain: restores 2-hop committed chain A->B->C when uncommitted reverse C->A re-promotion is evicted", async (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  const timers = createManualTimerQueue();
  const store = createMemoryStorage({
    [LS_ADMIN_SESSION_KEY]: makeSerializedSession({
      email: "staff.a@preservation.example.org",
      role: "staff",
      expOffsetSec: 3600,
    }),
  });
  const coord = createParcelSyncCoordinator({
    debounceMs: 650,
    scheduleTimer: timers.scheduleTimer,
    cancelTimer: timers.cancelTimer,
    sendDecisionRpc: async ({ parcelId, expectedUpdatedAt, patch, retryFn }) =>
      await callMonitoringRpc({
        webhookUrl: "https://example.org/exec",
        action: "upsert_parcel_decision",
        body: { parcel_id: parcelId, expected_updated_at: expectedUpdatedAt, ...patch },
        localStore: store,
        retryFn,
        fetchImpl: async (_url, init) => {
          const parsed = JSON.parse(init.body);
          return {
            ok: true,
            json: async () => ({
              ok: true,
              parcel: { parcel_id: parsed.parcel_id },
              decision: { parcel_id: parsed.parcel_id, decided_at: "2026-11-23T18:20:00.000Z" },
            }),
          };
        },
      }),
  });

  coord.promoteParcelKey("0420010000001", "CAMPUS:LOT_B", "");
  coord.promoteParcelKey("CAMPUS:LOT_B", "CAMPUS:PARISH_C", "");
  coord.enqueue("CAMPUS:PARISH_C", { screening_notes: "Committed C" }, "");
  await timers.popNextTimer()();

  store.setItem(
    LS_ADMIN_SESSION_KEY,
    makeSerializedSession({
      email: "staff.a@preservation.example.org",
      role: "staff",
      expOffsetSec: -120,
    })
  );
  coord.promoteParcelKey("CAMPUS:PARISH_C", "0420010000001", "");
  coord.enqueue("0420010000001", { screening_notes: "Uncommitted reverse" }, "");
  await timers.popNextTimer()();
  await replayPendingAuthRetries({
    session: { email: "screener@preservation.example.org", role: "screener", authorized: true },
    localStore: null,
  });

  assert.equal(coord.getPromotedTarget("0420010000001"), "CAMPUS:PARISH_C");
  assert.equal(coord.getPromotedTarget("CAMPUS:LOT_B"), "CAMPUS:PARISH_C");
});

test("UT-P1-SyncCoord-MidReplayReExpiryPreservesCausalQueue: settles without deadlocking on mid-replay re-expiry and replays causally once refreshed", async (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  const timers = createManualTimerQueue();
  const dispatched = [];
  const store = createMemoryStorage({
    [LS_ADMIN_SESSION_KEY]: makeSerializedSession({
      email: "staff.a@preservation.example.org",
      role: "staff",
      expOffsetSec: -120,
    }),
  });
  const coord = createParcelSyncCoordinator({
    debounceMs: 650,
    scheduleTimer: timers.scheduleTimer,
    cancelTimer: timers.cancelTimer,
    sendDecisionRpc: async ({ parcelId, expectedUpdatedAt, patch, retryFn }) =>
      await callMonitoringRpc({
        webhookUrl: "https://example.org/exec",
        action: "upsert_parcel_decision",
        body: { parcel_id: parcelId, expected_updated_at: expectedUpdatedAt, ...patch },
        localStore: store,
        retryFn,
        fetchImpl: async (_url, init) => {
          const parsed = JSON.parse(init.body);
          dispatched.push(parsed);
          const nextTs = `2026-11-23T19:00:0${dispatched.length}.000Z`;
          return {
            ok: true,
            json: async () => ({
              ok: true,
              parcel: { parcel_id: parsed.parcel_id },
              decision: { parcel_id: parsed.parcel_id, decided_at: nextTs },
            }),
          };
        },
      }),
  });

  coord.enqueue("0420010000001", { screening_notes: "Trace A1" }, "2026-11-23T18:50:00.000Z");
  await timers.popNextTimer()();
  coord.enqueue("CAMPUS:PARISH_A", { screening_notes: "Trace A2" }, "2026-11-23T18:50:00.000Z");
  await timers.popNextTimer()();
  coord.promoteParcelKey("0420010000001", "CAMPUS:PARISH_A", "2026-11-23T18:50:00.000Z");

  const stillExpiredReplay = await replayPendingAuthRetries({ localStore: store });
  const countAfterExpiredReplay = getPendingAuthRetryCount();

  store.setItem(
    LS_ADMIN_SESSION_KEY,
    makeSerializedSession({
      email: "staff.a@preservation.example.org",
      role: "staff",
      expOffsetSec: 3600,
    })
  );
  const validReplay = await replayPendingAuthRetries({ localStore: store });

  assert.equal(stillExpiredReplay.length, 1);
  assert.equal(stillExpiredReplay[0].result.queuedForRetry, true);
  assert.equal(countAfterExpiredReplay, 2);
  assert.equal(validReplay.length, 2);
  assert.deepEqual(
    dispatched.map((d) => d.parcel_id),
    ["0420010000001", "CAMPUS:PARISH_A"]
  );
  assert.equal(dispatched[1].expected_updated_at, "2026-11-23T19:00:01.000Z");
  assert.equal(coord.getPromotedTarget("0420010000001"), "CAMPUS:PARISH_A");
});

test("UT-P1-SyncCoord-ReverseFailureArrivalOrdersCausally: replays seq=1 before seq=2 when in-flight HTTP expiry arrives after preflight expiry", async (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  const timers = createManualTimerQueue();
  const httpFirstBatch = createDeferred();
  const dispatched = [];
  let fetchCount = 0;
  const store = createMemoryStorage({
    [LS_ADMIN_SESSION_KEY]: makeSerializedSession({
      email: "staff.a@preservation.example.org",
      role: "staff",
      expOffsetSec: 3600,
    }),
  });
  const coord = createParcelSyncCoordinator({
    debounceMs: 650,
    scheduleTimer: timers.scheduleTimer,
    cancelTimer: timers.cancelTimer,
    sendDecisionRpc: async ({ parcelId, expectedUpdatedAt, patch, retryFn }) =>
      await callMonitoringRpc({
        webhookUrl: "https://example.org/exec",
        action: "upsert_parcel_decision",
        body: { parcel_id: parcelId, expected_updated_at: expectedUpdatedAt, ...patch },
        localStore: store,
        retryFn,
        fetchImpl: async (_url, init) => {
          fetchCount += 1;
          if (fetchCount === 1) {
            const firstReply = await httpFirstBatch.promise;
            return { ok: true, json: async () => firstReply };
          }
          const parsed = JSON.parse(init.body);
          dispatched.push(parsed);
          return {
            ok: true,
            json: async () => ({
              ok: true,
              parcel: { parcel_id: parsed.parcel_id },
              decision: { parcel_id: parsed.parcel_id, decided_at: "2026-11-23T19:05:00.000Z" },
            }),
          };
        },
      }),
  });

  coord.enqueue("0420010000011", { screening_notes: "In-flight HTTP seq=1" }, "2026-11-23T18:55:00.000Z");
  const b1InFlight = timers.popNextTimer()();

  store.setItem(
    LS_ADMIN_SESSION_KEY,
    makeSerializedSession({
      email: "staff.a@preservation.example.org",
      role: "staff",
      expOffsetSec: -120,
    })
  );
  coord.enqueue("CAMPUS:PARISH_B", { screening_notes: "Preflight expired seq=2" }, "2026-11-23T18:55:00.000Z");
  await timers.popNextTimer()();
  httpFirstBatch.resolve({ authorized: false, error: "Invalid or expired token" });
  await b1InFlight;

  coord.promoteParcelKey("0420010000011", "CAMPUS:PARISH_B", "2026-11-23T18:55:00.000Z");
  store.setItem(
    LS_ADMIN_SESSION_KEY,
    makeSerializedSession({
      email: "staff.a@preservation.example.org",
      role: "staff",
      expOffsetSec: 3600,
    })
  );
  const replayed = await replayPendingAuthRetries({ localStore: store });

  assert.equal(replayed.length, 2);
  assert.deepEqual(
    dispatched.map((d) => d.parcel_id),
    ["0420010000011", "CAMPUS:PARISH_B"]
  );
});

test("UT-P1-SyncCoord-MultiHopTicketSerialization: serializes multi-hop A->B->C tickets and propagates serverTs across lineage", async (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  const timers = createManualTimerQueue();
  const hop1Gate = createDeferred();
  const multiHopOrder = [];
  const hopCoord = createParcelSyncCoordinator({
    debounceMs: 650,
    scheduleTimer: timers.scheduleTimer,
    cancelTimer: timers.cancelTimer,
    sendDecisionRpc: async ({ parcelId, expectedUpdatedAt, patch }) => {
      multiHopOrder.push({ parcelId, expectedUpdatedAt, note: patch.screening_notes });
      await hop1Gate.promise;
      const ts = `2026-11-23T20:00:0${multiHopOrder.length}.000Z`;
      return {
        ok: true,
        parcel: { parcel_id: parcelId },
        decision: { parcel_id: parcelId, decided_at: ts },
      };
    },
  });

  hopCoord.enqueue("0420010000021", { screening_notes: "hop1" }, "");
  const h1P = timers.popNextTimer()();
  hopCoord.enqueue("0420010000021", { screening_notes: "hop2" }, "");
  const h2P = timers.popNextTimer()();
  hopCoord.promoteParcelKey("0420010000021", "CAMPUS:HOP_LOT", "");
  hopCoord.enqueue("CAMPUS:HOP_LOT", { screening_notes: "hop3" }, "");
  const h3P = timers.popNextTimer()();
  hopCoord.promoteParcelKey("CAMPUS:HOP_LOT", "CAMPUS:HOP_ASSEMBLAGE", "");

  const pendingSnapshot = [
    hopCoord.hasPendingOrInFlight("0420010000021"),
    hopCoord.hasPendingOrInFlight("CAMPUS:HOP_LOT"),
    hopCoord.hasPendingOrInFlight("CAMPUS:HOP_ASSEMBLAGE"),
  ];
  hop1Gate.resolve();
  await Promise.all([h1P, h2P, h3P]);

  assert.deepEqual(pendingSnapshot, [true, true, true]);
  assert.deepEqual(
    multiHopOrder.map((m) => m.note),
    ["hop1", "hop2", "hop3"]
  );
  assert.equal(multiHopOrder[2].expectedUpdatedAt, "2026-11-23T20:00:02.000Z");
});

test("UT-P1-SyncCoord-TargetOnlyBatchMarksSourcePending: reports hasPendingOrInFlight on ancestor parcel ID when only promoted target has a pending batch", (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  const timers = createManualTimerQueue();
  const coord = createParcelSyncCoordinator({
    debounceMs: 650,
    scheduleTimer: timers.scheduleTimer,
    cancelTimer: timers.cancelTimer,
    sendDecisionRpc: async () => ({ ok: true }),
  });
  t.after(() => coord.clearAll());

  coord.promoteParcelKey("0420010000021", "CAMPUS:HOP_LOT", "");
  coord.promoteParcelKey("CAMPUS:HOP_LOT", "CAMPUS:HOP_ASSEMBLAGE", "");
  coord.enqueue("CAMPUS:HOP_ASSEMBLAGE", { screening_notes: "Target-only pending batch" }, "");

  assert.equal(coord.hasPendingOrInFlight("0420010000021"), true);
});

test("UT-P1-SyncCoord-InFlightHttpSwitchCancelsLateTokenExpiry: cancels valid-token coordinator batches and ignores late token-expiry after account switch", async (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  const timers = createManualTimerQueue();
  const inFlightBatch1 = createDeferred();
  const sentPayloads = [];
  let expiredCallbacks = 0;
  let callIndex = 0;
  const nowSec = Math.floor(Date.now() / 1000);
  const sharedLocalStore = createMemoryStorage({
    [LS_ADMIN_SESSION_KEY]: makeSerializedSession({
      email: "staff.a@preservation.example.org",
      role: "staff",
      expOffsetSec: 3600,
    }),
  });
  const sharedSessionStore = createMemoryStorage();
  const coord = createParcelSyncCoordinator({
    debounceMs: 650,
    scheduleTimer: timers.scheduleTimer,
    cancelTimer: timers.cancelTimer,
    sendDecisionRpc: async ({ parcelId, expectedUpdatedAt, patch, retryFn }) =>
      await callMonitoringRpc({
        webhookUrl: "https://example.org/exec",
        action: "upsert_parcel_decision",
        body: { parcel_id: parcelId, expected_updated_at: expectedUpdatedAt, ...patch },
        localStore: sharedLocalStore,
        sessionStore: sharedSessionStore,
        retryFn,
        onTokenExpired: () => {
          expiredCallbacks += 1;
        },
        fetchImpl: async (_url, init) => {
          callIndex += 1;
          const parsed = JSON.parse(init.body);
          sentPayloads.push(parsed);
          if (callIndex === 1) {
            const reply = await inFlightBatch1.promise;
            return { ok: true, json: async () => reply };
          }
          return {
            ok: true,
            json: async () => ({
              ok: true,
              parcel: { parcel_id: parsed.parcel_id },
              decision: { parcel_id: parsed.parcel_id, decided_at: "2026-11-23T21:00:00.000Z" },
            }),
          };
        },
      }),
  });

  coord.enqueue("0420010000031", { screening_notes: "User A HTTP in-flight" }, "");
  const f1Promise = timers.popNextTimer()();
  coord.enqueue("0420010000031", { screening_notes: "User A blocked batch 2" }, "");
  const f2Promise = timers.popNextTimer()();
  coord.enqueue("0420010000032", { screening_notes: "User A debounce timer" }, "");

  await verifyAndSignInMonitoring({
    webhookUrl: "https://example.org/exec",
    googleIdToken: makeSyntheticJwt({
      email: "screener@preservation.example.org",
      exp: nowSec + 3600,
    }),
    localStore: sharedLocalStore,
    sessionStore: sharedSessionStore,
    fetchImpl: async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        ok: true,
        authorized: true,
        email: "screener@preservation.example.org",
        role: "screener",
        supportsRoamingSync: false,
        supportsPhase1Monitoring: true,
      }),
    }),
  });
  inFlightBatch1.resolve({ authorized: false, error: "Invalid or expired token" });
  const [f1Res, f2Res] = await Promise.all([f1Promise, f2Promise]);
  coord.enqueue("0420010000031", { screening_notes: "Screener fresh edit" }, "");
  const scRes = await timers.popNextTimer()();

  assert.equal(timers.scheduled.size, 0);
  assert.equal(f2Res.cancelled, true);
  assert.equal(f1Res.queuedForRetry, false);
  assert.equal(f1Res.cancelled, true);
  assert.equal(expiredCallbacks, 0);
  assert.equal(getPendingAuthRetryCount(), 0);
  assert.equal(scRes.ok, true);
  assert.equal(sentPayloads.length, 2);
  assert.equal(sentPayloads[1].admin_email, "screener@preservation.example.org");
});

test("UT-P1-SyncCoord-MidReplayAbortPreservesNextGenCoordinatorBatch: keeps generation-1 coordinator promotions and timers intact when stale generation-0 replay HTTP settles", async (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  const timers = createManualTimerQueue();
  const staleReplayFetch = createDeferred();
  const nowSec = Math.floor(Date.now() / 1000);
  const localStore = createMemoryStorage({
    [LS_ADMIN_SESSION_KEY]: makeSerializedSession({
      email: "staff.a@preservation.example.org",
      role: "staff",
      expOffsetSec: -120,
    }),
  });
  const sessionStore = createMemoryStorage();
  let rpcCallCount = 0;
  const coord = createParcelSyncCoordinator({
    debounceMs: 650,
    scheduleTimer: timers.scheduleTimer,
    cancelTimer: timers.cancelTimer,
    sendDecisionRpc: async ({ parcelId, expectedUpdatedAt, patch, retryFn }) =>
      await callMonitoringRpc({
        webhookUrl: "https://example.org/exec",
        action: "upsert_parcel_decision",
        body: { parcel_id: parcelId, expected_updated_at: expectedUpdatedAt, ...patch },
        localStore,
        sessionStore,
        retryFn,
        fetchImpl: async (_url, init) => {
          rpcCallCount += 1;
          const parsed = JSON.parse(init.body);
          if (rpcCallCount === 1) {
            await staleReplayFetch.promise;
          }
          return {
            ok: true,
            json: async () => ({
              ok: true,
              parcel: { parcel_id: parsed.parcel_id },
              decision: { parcel_id: parsed.parcel_id, decided_at: "2026-11-23T22:00:00.000Z" },
            }),
          };
        },
      }),
  });

  coord.enqueue("0420090000089", { screening_notes: "Gen 0 expired batch" }, "");
  await timers.popNextTimer()();
  localStore.setItem(
    LS_ADMIN_SESSION_KEY,
    makeSerializedSession({
      email: "staff.a@preservation.example.org",
      role: "staff",
      expOffsetSec: 3600,
    })
  );
  const replayGen0 = replayPendingAuthRetries({ localStore, sessionStore });

  signOutMonitoringSession({ localStore, sessionStore });
  await verifyAndSignInMonitoring({
    webhookUrl: "https://example.org/exec",
    googleIdToken: makeSyntheticJwt({
      email: "staff.a@preservation.example.org",
      exp: nowSec + 7200,
    }),
    localStore,
    sessionStore,
    fetchImpl: async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        ok: true,
        authorized: true,
        email: "staff.a@preservation.example.org",
        role: "staff",
        supportsRoamingSync: false,
        supportsPhase1Monitoring: true,
      }),
    }),
  });
  coord.promoteParcelKey("0420090000089", "CAMPUS:GEN1_NEW", "");
  coord.enqueue("CAMPUS:GEN1_NEW", { screening_notes: "Gen 1 fresh edit" }, "");

  staleReplayFetch.resolve();
  await replayGen0;
  const promotedAfterStaleReplay = coord.getPromotedTarget("0420090000089");
  const scheduledBeforePop = timers.scheduled.size;
  const gen1Res = await timers.popNextTimer()();

  assert.equal(promotedAfterStaleReplay, "CAMPUS:GEN1_NEW");
  assert.equal(scheduledBeforePop, 1);
  assert.equal(gen1Res.ok, true);
});

test("UT-P1-AuthClient-PullStateExplicitSessionCancelledOnCrossTabSwitch: cancels explicit-session Staff pull before storage event when sibling tab switches to Screener", async (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  const pullGate = createDeferred();
  const nowSec = Math.floor(Date.now() / 1000);
  const staffJson = makeSerializedSession({
    email: "staff.a@preservation.example.org",
    role: "staff",
    expOffsetSec: 3600,
  });
  const sharedLocalStore = createMemoryStorage({ [LS_ADMIN_SESSION_KEY]: staffJson });
  const tab1SessionStore = createMemoryStorage({ [LS_ADMIN_SESSION_KEY]: staffJson });

  const pullPromise = pullCloudMonitoringState({
    webhookUrl: "https://example.org/exec",
    session: {
      authorized: true,
      email: "staff.a@preservation.example.org",
      googleIdToken: makeSyntheticJwt({
        email: "staff.a@preservation.example.org",
        exp: nowSec + 3600,
      }),
    },
    localStore: sharedLocalStore,
    sessionStore: tab1SessionStore,
    fetchImpl: async () => ({
      ok: true,
      json: async () => await pullGate.promise,
    }),
  });

  sharedLocalStore.setItem(
    LS_ADMIN_SESSION_KEY,
    makeSerializedSession({
      email: "screener@preservation.example.org",
      role: "screener",
      expOffsetSec: 3600,
    })
  );
  pullGate.resolve({
    ok: true,
    authorized: true,
    role: "staff",
    parcels: [{ parcel_id: "0152300000001" }],
    staffOnly: { parcelStrategy: [{ parcel_id: "0152300000001", strategy_notes: "CONFIDENTIAL" }] },
  });
  const stalePullResp = await pullPromise;

  assert.equal(stalePullResp.ok, false);
  assert.equal(stalePullResp.cancelled, true);
  assert.equal(
    sharedLocalStore.getItem(`${LS_AUTH_CACHE_PREFIX}staff.a@preservation.example.org`),
    null
  );
});

test("UT-P1-AuthClient-CrossTabSwitchPurgesForeignStaffCache: purges foreign Staff_Only cache keys on cross-tab account switch and removes storage listener on unsubscribe", (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  const fakeWin = createFakeStorageWindow();
  const sharedLocalStore = createMemoryStorage({
    [LS_ADMIN_SESSION_KEY]: makeSerializedSession({
      email: "staff.a@preservation.example.org",
      role: "staff",
      expOffsetSec: 3600,
    }),
    [`${LS_AUTH_CACHE_PREFIX}staff.a@preservation.example.org`]: '{"role":"staff","staffOnly":{"parcelStrategy":[{"strategy_notes":"SECRET"}]}}',
  });
  const tab1SessionStore = createMemoryStorage();
  const unsub = subscribeCrossTabAuthChanges(() => {}, fakeWin.windowStub, {
    localStore: sharedLocalStore,
    sessionStore: tab1SessionStore,
  });
  t.after(() => unsub());

  sharedLocalStore.setItem(
    LS_ADMIN_SESSION_KEY,
    makeSerializedSession({
      email: "screener@preservation.example.org",
      role: "screener",
      expOffsetSec: 3600,
    })
  );
  fakeWin.dispatchStorage({ key: LS_ADMIN_SESSION_KEY });
  unsub();

  assert.equal(fakeWin.listenerCount(), 0);
  assert.equal(
    sharedLocalStore.getItem(`${LS_AUTH_CACHE_PREFIX}staff.a@preservation.example.org`),
    null
  );
});

test("UT-P1-AuthClient-ScreenerSignInPurgesLeftoverStaffCache: evicts leftover Staff_Only cache when signing in as downgraded Screener", async (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  const nowSec = Math.floor(Date.now() / 1000);
  const localStore = createMemoryStorage({
    [`${LS_AUTH_CACHE_PREFIX}user@preservation.example.org`]:
      '{"role":"staff","staffOnly":{"parcelStrategy":[{"strategy_notes":"CONFIDENTIAL"}]}}',
  });
  const sessionStore = createMemoryStorage();

  const signInRes = await verifyAndSignInMonitoring({
    webhookUrl: "https://example.org/exec",
    googleIdToken: makeSyntheticJwt({
      email: "user@preservation.example.org",
      exp: nowSec + 3600,
    }),
    localStore,
    sessionStore,
    fetchImpl: async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        ok: true,
        authorized: true,
        email: "user@preservation.example.org",
        role: "screener",
        supportsRoamingSync: false,
        supportsPhase1Monitoring: true,
      }),
    }),
  });

  assert.equal(signInRes.ok, true);
  assert.equal(localStore.getItem(`${LS_AUTH_CACHE_PREFIX}user@preservation.example.org`), null);
});

test("UT-P1-AuthClient-ScreenerOverrideSignInPurgesLocalStaffCacheAndMirror: scrubs localStore Staff cache, legacy mirror, and foreign retries when sessionStore already held Screener", async (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  const nowSec = Math.floor(Date.now() / 1000);
  const localStore = createMemoryStorage({
    [LS_ADMIN_SESSION_KEY]: makeSerializedSession({
      email: "staff.old@preservation.example.org",
      role: "staff",
      expOffsetSec: 3600,
    }),
    [`${LS_AUTH_CACHE_PREFIX}user@preservation.example.org`]:
      '{"role":"staff","staffOnly":{"parcelStrategy":[{"strategy_notes":"CONFIDENTIAL"}]}}',
    ph_atlas_audit_decisions_v1: '{"0152300000001":{"status":"watch","at":1}}',
  });
  const sessionStore = createMemoryStorage({
    [LS_ADMIN_SESSION_KEY]: makeSerializedSession({
      email: "user@preservation.example.org",
      role: "screener",
      expOffsetSec: 1800,
    }),
  });
  let oldStaffRetryCancelled = false;
  const oldStaffRetryFn = async () => ({ ok: true });
  oldStaffRetryFn.cancelQueuedRetry = () => {
    oldStaffRetryCancelled = true;
  };
  queuePendingAuthRetry(
    oldStaffRetryFn,
    "foreign_old_staff_op",
    { authorized: true, email: "staff.old@preservation.example.org", role: "staff" },
    { localStore, sessionStore }
  );

  const signInRes = await verifyAndSignInMonitoring({
    webhookUrl: "https://example.org/exec",
    googleIdToken: makeSyntheticJwt({
      email: "user@preservation.example.org",
      exp: nowSec + 3600,
    }),
    localStore,
    sessionStore,
    fetchImpl: async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        ok: true,
        authorized: true,
        email: "user@preservation.example.org",
        role: "screener",
        supportsRoamingSync: false,
        supportsPhase1Monitoring: true,
      }),
    }),
  });

  assert.equal(signInRes.ok, true);
  assert.equal(localStore.getItem(`${LS_AUTH_CACHE_PREFIX}user@preservation.example.org`), null);
  assert.equal(localStore.getItem("ph_atlas_audit_decisions_v1"), null);
  assert.equal(oldStaffRetryCancelled, true);
  assert.equal(getPendingAuthRetryCount(), 0);
});

test("UT-P1-AuthClient-PullDowngradeOverwritesStaffCache: overwrites localStore Staff cache without staffOnly when pull_monitoring_state returns role screener", async (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  const localStore = createMemoryStorage({
    [LS_ADMIN_SESSION_KEY]: makeSerializedSession({
      email: "user@preservation.example.org",
      role: "staff",
      expOffsetSec: 3600,
    }),
  });
  saveCachedMonitoringState(
    "user@preservation.example.org",
    "staff",
    { staffOnly: { parcelStrategy: [{ strategy_notes: "OLD_STAFF" }] } },
    { localStore }
  );

  await pullCloudMonitoringState({
    webhookUrl: "https://example.org/exec",
    localStore,
    fetchImpl: async () => ({
      ok: true,
      json: async () => ({
        ok: true,
        authorized: true,
        role: "screener",
        parcels: [{ parcel_id: "0152300000001" }],
      }),
    }),
  });

  const cachedAfterDowngrade = JSON.parse(
    localStore.getItem(`${LS_AUTH_CACHE_PREFIX}user@preservation.example.org`)
  );
  assert.equal(cachedAfterDowngrade.role, "screener");
  assert.equal("staffOnly" in cachedAfterDowngrade, false);
});

test("UT-P1-AuthClient-UnnormalizedExplicitSessionRoleIsolation: enforces default staff role ownership when explicit session omits role", async (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  let executed = false;
  queuePendingAuthRetry(
    async () => {
      executed = true;
      return { ok: true };
    },
    "omitted_role_staff_op",
    { authorized: true, email: "user@preservation.example.org" },
    { localStore: null }
  );

  const replayAsScreener = await replayPendingAuthRetries({
    session: { authorized: true, email: "user@preservation.example.org", role: "screener" },
    localStore: null,
  });

  assert.equal(replayAsScreener.length, 0);
  assert.equal(executed, false);
  assert.equal(getPendingAuthRetryCount(), 0);
});

test("UT-P1-AuthClient-TabScopedStaffPullDoesNotPersistToSharedLocalStore: keeps tab-scoped Staff pull in memory without writing Staff_Only cache to Screener localStorage", async (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  const screenerSharedLocal = createMemoryStorage({
    [LS_ADMIN_SESSION_KEY]: makeSerializedSession({
      email: "screener@preservation.example.org",
      role: "screener",
      expOffsetSec: 3600,
    }),
  });
  const tabScopedStaffSession = createMemoryStorage({
    [LS_ADMIN_SESSION_KEY]: makeSerializedSession({
      email: "staff.tab@preservation.example.org",
      role: "staff",
      expOffsetSec: 3600,
    }),
  });

  const resp = await pullCloudMonitoringState({
    webhookUrl: "https://example.org/exec",
    localStore: screenerSharedLocal,
    sessionStore: tabScopedStaffSession,
    fetchImpl: async () => ({
      ok: true,
      json: async () => ({
        ok: true,
        authorized: true,
        role: "staff",
        parcels: [{ parcel_id: "0152300000001" }],
        staffOnly: { parcelStrategy: [{ parcel_id: "0152300000001", strategy_notes: "TAB_ONLY" }] },
      }),
    }),
  });

  assert.equal(resp.ok, true);
  assert.equal(
    screenerSharedLocal.getItem(`${LS_AUTH_CACHE_PREFIX}staff.tab@preservation.example.org`),
    null
  );
});

test("UT-P1-AuthClient-InFlightVerifyCancelledOnSignOut: aborts slow verify_admin response when signOutMonitoringSession runs mid-flight", async (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  const verifyGate = createDeferred();
  const nowSec = Math.floor(Date.now() / 1000);
  const localStore = createMemoryStorage({
    [LS_ADMIN_SESSION_KEY]: makeSerializedSession({
      email: "staff@preservation.example.org",
      role: "staff",
      expOffsetSec: 300,
    }),
  });
  const sessionStore = createMemoryStorage();

  const verifyPromise = verifyAndSignInMonitoring({
    webhookUrl: "https://example.org/exec",
    googleIdToken: makeSyntheticJwt({
      email: "staff@preservation.example.org",
      exp: nowSec + 3600,
    }),
    localStore,
    sessionStore,
    fetchImpl: async () => ({
      ok: true,
      status: 200,
      json: async () => await verifyGate.promise,
    }),
  });

  signOutMonitoringSession({ localStore, sessionStore });
  verifyGate.resolve({
    ok: true,
    authorized: true,
    email: "staff@preservation.example.org",
    role: "staff",
    googleClientId: "late-client-id.apps.googleusercontent.com",
    supportsRoamingSync: false,
    supportsPhase1Monitoring: true,
  });
  const verifyRes = await verifyPromise;

  assert.equal(verifyRes.ok, false);
  assert.equal(verifyRes.cancelled, true);
  assert.equal(readActiveMonitoringSession({ localStore, sessionStore }), null);
  assert.equal(localStore.getItem(LS_CONFIGURED_GOOGLE_CLIENT_ID_KEY), null);
});

test("UT-P1-AuthClient-CrossTabMirrorRevokedOnSimulatedSiblingSignOut: evicts mirrored sessionStore and cancels Tab 1 coordinator timer on cross-process sibling sign-out", async (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  const timers = createManualTimerQueue();
  const fakeWin = createFakeStorageWindow();
  const nowSec = Math.floor(Date.now() / 1000);
  const sharedLocalA = createMemoryStorage();
  const tab1SessionA = createMemoryStorage();
  const modeAEvents = [];
  const unsub = subscribeCrossTabAuthChanges(
    (payload) => modeAEvents.push(payload),
    fakeWin.windowStub,
    { localStore: sharedLocalA, sessionStore: tab1SessionA }
  );
  t.after(() => unsub());

  await verifyAndSignInMonitoring({
    webhookUrl: "https://example.org/exec",
    googleIdToken: makeSyntheticJwt({
      email: "staff@preservation.example.org",
      exp: nowSec + 3600,
    }),
    localStore: sharedLocalA,
    sessionStore: tab1SessionA,
    fetchImpl: async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        ok: true,
        authorized: true,
        email: "staff@preservation.example.org",
        role: "staff",
        supportsRoamingSync: false,
        supportsPhase1Monitoring: true,
      }),
    }),
  });

  const coordA = createParcelSyncCoordinator({
    debounceMs: 650,
    scheduleTimer: timers.scheduleTimer,
    cancelTimer: timers.cancelTimer,
    sendDecisionRpc: async () => ({ ok: true }),
  });
  t.after(() => coordA.clearAll());
  coordA.enqueue("0420010000033", { screening_notes: "Tab 1 pending before Tab 2 sign-out" }, "");

  sharedLocalA.removeItem(LS_ADMIN_SESSION_KEY);
  sharedLocalA.setItem(
    LS_CROSS_TAB_BROADCAST_KEY,
    JSON.stringify({ eventType: "sign_out", role: "public", email: "", at: Date.now() })
  );
  const scheduledBeforeBroadcast = timers.scheduled.size;
  fakeWin.dispatchStorage({ key: LS_CROSS_TAB_BROADCAST_KEY });

  assert.equal(scheduledBeforeBroadcast, 1);
  assert.equal(tab1SessionA.getItem(LS_ADMIN_SESSION_KEY), null);
  assert.equal(readActiveMonitoringSession({ localStore: sharedLocalA, sessionStore: tab1SessionA }), null);
  assert.equal(modeAEvents.at(-1)?.signedOut, true);
  assert.equal(modeAEvents.at(-1)?.role, "public");
  assert.equal(timers.scheduled.size, 0);
});

test("UT-P1-AuthClient-TabScopedScreenerOverrideSurvivesSiblingRefresh: preserves sessionStore-only Screener sign-in and active coordinator batch across sibling Staff token refresh", async (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  const timers = createManualTimerQueue();
  const fakeWin = createFakeStorageWindow();
  const nowSec = Math.floor(Date.now() / 1000);
  const sharedLocalB = createMemoryStorage({
    [LS_ADMIN_SESSION_KEY]: makeSerializedSession({
      email: "staff.a@preservation.example.org",
      role: "staff",
      expOffsetSec: 1800,
    }),
  });
  const screenerTabSessionB = createMemoryStorage();
  const modeBEvents = [];
  const unsub = subscribeCrossTabAuthChanges(
    (payload) => modeBEvents.push(payload),
    fakeWin.windowStub,
    { localStore: sharedLocalB, sessionStore: screenerTabSessionB }
  );
  t.after(() => unsub());

  await verifyAndSignInMonitoring({
    webhookUrl: "https://example.org/exec",
    googleIdToken: makeSyntheticJwt({
      email: "screener@preservation.example.org",
      exp: nowSec + 3600,
    }),
    sessionStore: screenerTabSessionB,
    fetchImpl: async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        ok: true,
        authorized: true,
        email: "screener@preservation.example.org",
        role: "screener",
        supportsRoamingSync: false,
        supportsPhase1Monitoring: true,
      }),
    }),
  });
  const coordB = createParcelSyncCoordinator({
    debounceMs: 650,
    scheduleTimer: timers.scheduleTimer,
    cancelTimer: timers.cancelTimer,
    sendDecisionRpc: async () => ({ ok: true }),
  });
  t.after(() => coordB.clearAll());
  coordB.enqueue("0420010000044", { screening_notes: "Tab-scoped Screener edit" }, "");

  sharedLocalB.setItem(
    LS_ADMIN_SESSION_KEY,
    makeSerializedSession({
      email: "staff.a@preservation.example.org",
      role: "staff",
      expOffsetSec: 7200,
    })
  );
  fakeWin.dispatchStorage({ key: LS_ADMIN_SESSION_KEY });

  assert.ok(screenerTabSessionB.getItem(LS_ADMIN_SESSION_KEY));
  assert.equal(modeBEvents.at(-1)?.role, "screener");
  assert.equal(modeBEvents.at(-1)?.signedOut, false);
  assert.equal(timers.scheduled.size, 1);
});

test("UT-P1-AuthClient-MidReplayCrossTabSwitchRequeuesIncomingOwner: cancels prior-owner tail retries and re-queues incoming-owner retries on cross-tab switch", async (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  const headGate = createDeferred();
  const fakeWin = createFakeStorageWindow();
  const sharedLocal = createMemoryStorage({
    [LS_ADMIN_SESSION_KEY]: makeSerializedSession({
      email: "staff.a@preservation.example.org",
      role: "staff",
      expOffsetSec: 3600,
    }),
  });
  const sharedSession = createMemoryStorage();
  const unsub = subscribeCrossTabAuthChanges(() => {}, fakeWin.windowStub, {
    localStore: sharedLocal,
    sessionStore: sharedSession,
  });
  t.after(() => unsub());

  let staffA2Executed = false;
  let staffA2Cancelled = false;
  let screenerExecuted = false;
  queuePendingAuthRetry(
    async () => await headGate.promise,
    "staff_a_1",
    { email: "staff.a@preservation.example.org", role: "staff", authorized: true },
    { localStore: sharedLocal, sessionStore: sharedSession }
  );
  const staffA2Fn = async () => {
    staffA2Executed = true;
    return { ok: true };
  };
  staffA2Fn.cancelQueuedRetry = () => {
    staffA2Cancelled = true;
  };
  queuePendingAuthRetry(
    staffA2Fn,
    "staff_a_2",
    { email: "staff.a@preservation.example.org", role: "staff", authorized: true },
    { localStore: sharedLocal, sessionStore: sharedSession }
  );
  queuePendingAuthRetry(
    async () => {
      screenerExecuted = true;
      return { ok: true };
    },
    "screener_op",
    { email: "screener@preservation.example.org", role: "screener", authorized: true },
    { localStore: sharedLocal, sessionStore: sharedSession }
  );

  const replayInFlight = replayPendingAuthRetries({
    localStore: sharedLocal,
    sessionStore: sharedSession,
  });
  sharedLocal.setItem(
    LS_ADMIN_SESSION_KEY,
    makeSerializedSession({
      email: "screener@preservation.example.org",
      role: "screener",
      expOffsetSec: 3600,
    })
  );
  fakeWin.dispatchStorage({ key: LS_ADMIN_SESSION_KEY });
  headGate.resolve({ ok: true });
  await replayInFlight;
  const countAfterSwitchAbort = getPendingAuthRetryCount();
  await replayPendingAuthRetries({ localStore: sharedLocal, sessionStore: sharedSession });

  assert.equal(staffA2Executed, false);
  assert.equal(staffA2Cancelled, true);
  assert.equal(countAfterSwitchAbort, 1);
  assert.equal(screenerExecuted, true);
});

test("UT-P1-AuthClient-MidReplayPostAwaitChildEviction: evicts post-await child retry queued by invalidated closure while preserving new-generation foreign retry", async (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  const headGate = createDeferred();
  let staleChildCancelled = false;
  let newGenBExecuted = false;
  const staleChildFn = async () => ({ ok: true });
  staleChildFn.cancelQueuedRetry = () => {
    staleChildCancelled = true;
  };
  queuePendingAuthRetry(
    async () => {
      await headGate.promise;
      queuePendingAuthRetry(
        staleChildFn,
        "post_await_stale_child",
        { email: "staff.a@preservation.example.org", role: "staff", authorized: true },
        { localStore: null }
      );
      throw new Error("simulated network abort");
    },
    "throwing_head_op",
    { email: "staff.a@preservation.example.org", role: "staff", authorized: true },
    { localStore: null }
  );

  const catchReplayPromise = replayPendingAuthRetries({
    session: { email: "staff.a@preservation.example.org", role: "staff", authorized: true },
    localStore: null,
  });
  clearPendingAuthRetries();
  queuePendingAuthRetry(
    async () => {
      newGenBExecuted = true;
      return { ok: true };
    },
    "gen_new_staff_b_op",
    { email: "staff.b@preservation.example.org", role: "staff", authorized: true },
    { localStore: null }
  );
  headGate.resolve();
  await catchReplayPromise;
  const countAfterCatchAbort = getPendingAuthRetryCount();
  await replayPendingAuthRetries({
    session: { email: "staff.b@preservation.example.org", role: "staff", authorized: true },
    localStore: null,
  });

  assert.equal(staleChildCancelled, true);
  assert.equal(countAfterCatchAbort, 1);
  assert.equal(newGenBExecuted, true);
});

test("UT-P1-AuthClient-MidReplaySameUserReSignInPreservesNewRetry: cancels old-epoch tail retries while preserving new retries queued after same-user re-sign-in", async (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  const headGate = createDeferred();
  const nowSec = Math.floor(Date.now() / 1000);
  const localStore = createMemoryStorage({
    [LS_ADMIN_SESSION_KEY]: makeSerializedSession({
      email: "staff.a@preservation.example.org",
      role: "staff",
      expOffsetSec: 3600,
    }),
  });
  const sessionStore = createMemoryStorage();
  let oldEpochTailCancelled = false;
  let newEpochSameUserExecuted = false;
  let newEpochSameUserCancelled = false;

  queuePendingAuthRetry(
    async () => await headGate.promise,
    "epoch0_head",
    { email: "staff.a@preservation.example.org", role: "staff", authorized: true },
    { localStore, sessionStore }
  );
  const oldEpochTailFn = async () => ({ ok: true });
  oldEpochTailFn.cancelQueuedRetry = () => {
    oldEpochTailCancelled = true;
  };
  queuePendingAuthRetry(
    oldEpochTailFn,
    "epoch0_tail",
    { email: "staff.a@preservation.example.org", role: "staff", authorized: true },
    { localStore, sessionStore }
  );

  const replayEpoch0 = replayPendingAuthRetries({ localStore, sessionStore });

  signOutMonitoringSession({ localStore, sessionStore });
  const reSignIn = await verifyAndSignInMonitoring({
    webhookUrl: "https://example.org/exec",
    googleIdToken: makeSyntheticJwt({
      email: "staff.a@preservation.example.org",
      exp: nowSec + 7200,
    }),
    localStore,
    sessionStore,
    fetchImpl: async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        ok: true,
        authorized: true,
        email: "staff.a@preservation.example.org",
        role: "staff",
        supportsRoamingSync: false,
        supportsPhase1Monitoring: true,
      }),
    }),
  });

  const newEpochFn = async () => {
    newEpochSameUserExecuted = true;
    return { ok: true };
  };
  newEpochFn.cancelQueuedRetry = () => {
    newEpochSameUserCancelled = true;
  };
  queuePendingAuthRetry(newEpochFn, "epoch2_new_edit", reSignIn.session, {
    localStore,
    sessionStore,
  });

  headGate.resolve({ ok: true });
  await replayEpoch0;
  const countAfterEpoch0Abort = getPendingAuthRetryCount();
  await replayPendingAuthRetries({ localStore, sessionStore });

  assert.equal(oldEpochTailCancelled, true);
  assert.equal(newEpochSameUserCancelled, false);
  assert.equal(countAfterEpoch0Abort, 1);
  assert.equal(newEpochSameUserExecuted, true);
});

test("UT-P1-AuthClient-MidReplayPreStorageCrossTabDowngradeCancelsTail: aborts queued Staff tail retries when sibling tab mutates localStorage to Screener before storage macrotask fires", async (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  const headFetchGate = createDeferred();
  const dispatchedPayloads = [];
  const staffSerialized = makeSerializedSession({
    email: "staff.a@preservation.example.org",
    role: "staff",
    expOffsetSec: 3600,
  });
  const sharedLocal = createMemoryStorage({ [LS_ADMIN_SESSION_KEY]: staffSerialized });
  const tabSession = createMemoryStorage({ [LS_ADMIN_SESSION_KEY]: staffSerialized });
  let staffTailCancelled = false;
  let screenerRetryExecuted = false;

  const staffRetry1 = async () =>
    await callMonitoringRpc({
      webhookUrl: "https://example.org/exec",
      action: "upsert_parcel_decision",
      body: { parcel_id: "0420010000051", screening_notes: "Staff retry 1" },
      localStore: sharedLocal,
      sessionStore: tabSession,
      fetchImpl: async (_url, init) => {
        dispatchedPayloads.push(JSON.parse(init.body));
        await headFetchGate.promise;
        return { ok: true, json: async () => ({ ok: true }) };
      },
    });
  const staffRetry2 = async () =>
    await callMonitoringRpc({
      webhookUrl: "https://example.org/exec",
      action: "upsert_parcel_decision",
      body: { parcel_id: "0420010000052", screening_notes: "Staff retry 2" },
      localStore: sharedLocal,
      sessionStore: tabSession,
      fetchImpl: async (_url, init) => {
        dispatchedPayloads.push(JSON.parse(init.body));
        return { ok: true, json: async () => ({ ok: true }) };
      },
    });
  staffRetry2.cancelQueuedRetry = () => {
    staffTailCancelled = true;
  };
  queuePendingAuthRetry(
    staffRetry1,
    "staff_pre_storage_1",
    { authorized: true, email: "staff.a@preservation.example.org", role: "staff" },
    { localStore: sharedLocal, sessionStore: tabSession }
  );
  queuePendingAuthRetry(
    staffRetry2,
    "staff_pre_storage_2",
    { authorized: true, email: "staff.a@preservation.example.org", role: "staff" },
    { localStore: sharedLocal, sessionStore: tabSession }
  );
  queuePendingAuthRetry(
    async () => {
      screenerRetryExecuted = true;
      return { ok: true };
    },
    "screener_tail_op",
    { authorized: true, email: "screener@preservation.example.org", role: "screener" },
    { localStore: sharedLocal, sessionStore: tabSession }
  );

  const firstReplayPromise = replayPendingAuthRetries({
    localStore: sharedLocal,
    sessionStore: tabSession,
  });
  sharedLocal.setItem(
    LS_ADMIN_SESSION_KEY,
    makeSerializedSession({
      email: "screener@preservation.example.org",
      role: "screener",
      expOffsetSec: 3600,
    })
  );
  headFetchGate.resolve();
  await firstReplayPromise;
  const countAfterPreStorageAbort = getPendingAuthRetryCount();
  await replayPendingAuthRetries({ localStore: sharedLocal, sessionStore: tabSession });

  assert.equal(dispatchedPayloads.length, 1);
  assert.equal(dispatchedPayloads[0].parcel_id, "0420010000051");
  assert.equal(dispatchedPayloads[0].admin_email, "staff.a@preservation.example.org");
  assert.equal(staffTailCancelled, true);
  assert.equal(tabSession.getItem(LS_ADMIN_SESSION_KEY), null);
  assert.equal(countAfterPreStorageAbort, 1);
  assert.equal(screenerRetryExecuted, true);
});

test("UT-P1-SyncCoord-ExpiredPrePromotionBatchDoesNotCommitSubsequentMerge: rolls back post-batch1 campus promotion when replayed pre-promotion batch1 succeeds and batch2 is evicted", async (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  const timers = createManualTimerQueue();
  const sentPayloads = [];
  const store = createMemoryStorage({
    [LS_ADMIN_SESSION_KEY]: makeSerializedSession({
      email: "staff.a@preservation.example.org",
      role: "staff",
      expOffsetSec: -120,
    }),
  });
  const coord = createParcelSyncCoordinator({
    debounceMs: 650,
    scheduleTimer: timers.scheduleTimer,
    cancelTimer: timers.cancelTimer,
    sendDecisionRpc: async ({ parcelId, expectedUpdatedAt, patch, retryFn }) =>
      await callMonitoringRpc({
        webhookUrl: "https://example.org/exec",
        action: "upsert_parcel_decision",
        body: { parcel_id: parcelId, expected_updated_at: expectedUpdatedAt, ...patch },
        localStore: store,
        retryFn,
        fetchImpl: async (_url, init) => {
          const parsed = JSON.parse(init.body);
          sentPayloads.push(parsed);
          // Re-expire stored token as batch1 resolves so unblocked batch2 queues into _pendingAuthRetryQueue.
          store.setItem(
            LS_ADMIN_SESSION_KEY,
            makeSerializedSession({
              email: "staff.a@preservation.example.org",
              role: "staff",
              expOffsetSec: -120,
            })
          );
          return {
            ok: true,
            json: async () => ({
              ok: true,
              parcel: { parcel_id: parsed.parcel_id },
              decision: { parcel_id: parsed.parcel_id, decided_at: "2026-11-23T23:00:00.000Z" },
            }),
          };
        },
      }),
  });
  t.after(() => coord.clearAll());

  coord.enqueue("CAMPUS:ST_THOMAS", { screening_notes: "Campus note before merge" }, "");
  await timers.popNextTimer()();
  coord.promoteParcelKey("0420010000099", "CAMPUS:ST_THOMAS", "");
  coord.enqueue(
    "CAMPUS:ST_THOMAS",
    { parcel_id: "CAMPUS:ST_THOMAS", replace_parcel_id: "0420010000099", screening_notes: "Merged parcel" },
    ""
  );
  const batch2TicketPromise = timers.popNextTimer()();

  store.setItem(
    LS_ADMIN_SESSION_KEY,
    makeSerializedSession({
      email: "staff.a@preservation.example.org",
      role: "staff",
      expOffsetSec: 3600,
    })
  );
  await replayPendingAuthRetries({ localStore: store });
  await batch2TicketPromise;
  await replayPendingAuthRetries({
    session: { authorized: true, email: "screener@preservation.example.org", role: "screener" },
    localStore: null,
  });

  assert.equal(sentPayloads.length, 1);
  assert.equal(sentPayloads[0].screening_notes, "Campus note before merge");
  assert.equal(coord.getPromotedTarget("0420010000099"), "0420010000099");
});

test("UT-P1-AuthClient-PullDowngradePurgesStaffCacheAndRetriesOnQuotaError: downgrades stored role, evicts Staff retries, and purges staffOnly cache even if cache write throws QuotaExceededError", async (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  const cacheKey = `${LS_AUTH_CACHE_PREFIX}user@preservation.example.org`;
  const localStore = createMemoryStorage({
    [LS_ADMIN_SESSION_KEY]: makeSerializedSession({
      email: "user@preservation.example.org",
      role: "staff",
      expOffsetSec: 3600,
    }),
    [cacheKey]: '{"role":"staff","staffOnly":{"parcelStrategy":[{"strategy_notes":"CONFIDENTIAL"}]}}',
  });
  const origSetItem = localStore.setItem.bind(localStore);
  localStore.setItem = (k, v) => {
    if (String(k).startsWith(LS_AUTH_CACHE_PREFIX)) {
      throw new Error("QuotaExceededError");
    }
    origSetItem(k, v);
  };
  let staffRetryCancelled = false;
  const staffRetryFn = async () => ({ ok: true });
  staffRetryFn.cancelQueuedRetry = () => {
    staffRetryCancelled = true;
  };
  queuePendingAuthRetry(
    staffRetryFn,
    "staff_only_mutation",
    { authorized: true, email: "user@preservation.example.org", role: "staff" },
    { localStore }
  );

  const pullResp = await pullCloudMonitoringState({
    webhookUrl: "https://example.org/exec",
    localStore,
    fetchImpl: async () => ({
      ok: true,
      json: async () => ({
        ok: true,
        authorized: true,
        role: " Screener ",
        parcels: [{ parcel_id: "0152300000001" }],
        staffOnly: { parcelStrategy: [{ strategy_notes: "SHOULD_BE_STRIPPED" }] },
      }),
    }),
  });

  assert.equal(pullResp.ok, true);
  assert.equal("staffOnly" in pullResp, false);
  assert.equal(localStore.getItem(cacheKey), null);
  assert.equal(getActiveMonitoringRole({ localStore }), "screener");
  assert.equal(canAccessStaffOnly({ localStore }), false);
  assert.equal(staffRetryCancelled, true);
  assert.equal(getPendingAuthRetryCount(), 0);
});

test("UT-P1-AuthClient-ExplicitUnauthorizedSessionRejectedAcrossEntryPoints: rejects explicit unauthorized session objects without falling back to ambient localStore Staff session", async (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  const localStore = createMemoryStorage({
    [LS_ADMIN_SESSION_KEY]: makeSerializedSession({
      email: "staff.a@preservation.example.org",
      role: "staff",
      expOffsetSec: 3600,
    }),
  });
  const unauthExplicit = {
    authorized: false,
    email: "staff.a@preservation.example.org",
    role: "staff",
  };
  let fnCancelled = false;
  const retryFn = async () => ({ ok: true });
  retryFn.cancelQueuedRetry = () => {
    fnCancelled = true;
  };

  const queuedCount = queuePendingAuthRetry(retryFn, "unauth_explicit_op", unauthExplicit, {
    localStore,
  });
  const rpcRes = await callMonitoringRpc({
    webhookUrl: "https://example.org/exec",
    action: "upsert_parcel_decision",
    session: unauthExplicit,
    localStore,
    fetchImpl: async () => ({ ok: true, json: async () => ({ ok: true }) }),
  });
  const pullRes = await pullCloudMonitoringState({
    webhookUrl: "https://example.org/exec",
    session: unauthExplicit,
    localStore,
    fetchImpl: async () => ({ ok: true, json: async () => ({ ok: true }) }),
  });

  assert.equal(fnCancelled, true);
  assert.equal(queuedCount, 0);
  assert.equal(getPendingAuthRetryCount(), 0);
  assert.equal(rpcRes.authorized, false);
  assert.equal(pullRes.signedOut, true);
});

test("UT-P1-SyncCoord-NonRootCycleEvictionRestoresIntermediateCommittedEdge: restores intermediate A->B edge in committed A->B->C chain when uncommitted C->A then C->D promotions are evicted", async (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  const timers = createManualTimerQueue();
  const store = createMemoryStorage({
    [LS_ADMIN_SESSION_KEY]: makeSerializedSession({
      email: "staff.a@preservation.example.org",
      role: "staff",
      expOffsetSec: 3600,
    }),
  });
  const coord = createParcelSyncCoordinator({
    debounceMs: 650,
    scheduleTimer: timers.scheduleTimer,
    cancelTimer: timers.cancelTimer,
    sendDecisionRpc: async ({ parcelId, expectedUpdatedAt, patch, retryFn }) =>
      await callMonitoringRpc({
        webhookUrl: "https://example.org/exec",
        action: "upsert_parcel_decision",
        body: { parcel_id: parcelId, expected_updated_at: expectedUpdatedAt, ...patch },
        localStore: store,
        retryFn,
        fetchImpl: async (_url, init) => {
          const parsed = JSON.parse(init.body);
          return {
            ok: true,
            json: async () => ({
              ok: true,
              parcel: { parcel_id: parsed.parcel_id },
              decision: { parcel_id: parsed.parcel_id, decided_at: "2026-11-23T23:30:00.000Z" },
            }),
          };
        },
      }),
  });
  t.after(() => coord.clearAll());

  coord.promoteParcelKey("0420010000001", "CAMPUS:LOT_B", "");
  coord.promoteParcelKey("CAMPUS:LOT_B", "CAMPUS:PARISH_C", "");
  coord.enqueue("CAMPUS:PARISH_C", { screening_notes: "Committed A->B->C" }, "");
  await timers.popNextTimer()();

  store.setItem(
    LS_ADMIN_SESSION_KEY,
    makeSerializedSession({
      email: "staff.a@preservation.example.org",
      role: "staff",
      expOffsetSec: -120,
    })
  );
  coord.promoteParcelKey("CAMPUS:PARISH_C", "0420010000001", "");
  coord.promoteParcelKey("CAMPUS:PARISH_C", "CAMPUS:NEW_D", "");
  coord.enqueue("CAMPUS:NEW_D", { screening_notes: "Uncommitted C->D" }, "");
  await timers.popNextTimer()();
  await replayPendingAuthRetries({
    session: { authorized: true, email: "screener@preservation.example.org", role: "screener" },
    localStore: null,
  });

  assert.equal(coord.getPromotedTarget("0420010000001"), "CAMPUS:PARISH_C");
  assert.equal(coord.getPromotedTarget("CAMPUS:LOT_B"), "CAMPUS:PARISH_C");
  assert.equal(coord.getPromotedTarget("CAMPUS:PARISH_C"), "CAMPUS:PARISH_C");
});

test("UT-P1-AuthClient-PurgeInfersActiveScreenerRole: evicts leftover Staff_Only cache when preserveRole is omitted and active session is Screener", (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  const cacheKey = `${LS_AUTH_CACHE_PREFIX}user@preservation.example.org`;
  const localStore = createMemoryStorage({
    [LS_ADMIN_SESSION_KEY]: JSON.stringify({
      authorized: true,
      email: "user@preservation.example.org",
      role: " Screener ",
    }),
    [cacheKey]: '{"role":"staff","staffOnly":{"parcelStrategy":[{"strategy_notes":"LEFTOVER"}]}}',
  });

  purgePrivateMonitoringStorage({
    localStore,
    preserveEmail: "user@preservation.example.org",
  });

  assert.equal(localStore.getItem(cacheKey), null);
});

test("UT-P1-AuthClient-CacheHelpersNormalizePaddedScreenerRole: strips staffOnly and moderation count when role has whitespace or mixed case", (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  const localStore = createMemoryStorage();

  const savedOk = saveCachedMonitoringState(
    "user@preservation.example.org",
    " Screener ",
    {
      parcels: [{ parcel_id: "0152300000001" }],
      staffOnly: { parcelStrategy: [{ strategy_notes: "SECRET" }] },
      pendingModerationCount: 3,
    },
    { localStore }
  );
  const loaded = loadCachedMonitoringState(
    "user@preservation.example.org",
    " Screener ",
    { localStore }
  );

  assert.equal(savedOk, true);
  assert.equal(loaded.role, "screener");
  assert.equal("staffOnly" in loaded, false);
  assert.equal("pendingModerationCount" in loaded, false);
});

test("UT-P1-AuthClient-InFlightRpc200CancelledOnSignOut: rejects 200 OK Staff RPC response when signOutMonitoringSession runs mid-flight", async (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  const rpcGate = createDeferred();
  const staffSerialized = makeSerializedSession({
    email: "staff.a@preservation.example.org",
    role: "staff",
    expOffsetSec: 3600,
  });
  const localStore = createMemoryStorage({ [LS_ADMIN_SESSION_KEY]: staffSerialized });
  const sessionStore = createMemoryStorage({ [LS_ADMIN_SESSION_KEY]: staffSerialized });

  const rpcPromise = callMonitoringRpc({
    webhookUrl: "https://example.org/exec",
    action: "list_pending_moderation",
    localStore,
    sessionStore,
    fetchImpl: async () => ({
      ok: true,
      json: async () => await rpcGate.promise,
    }),
  });
  signOutMonitoringSession({ localStore, sessionStore });
  rpcGate.resolve({
    ok: true,
    items: [{ row_index: 2, hcad_num: "0152300000001", secret_note: "STAFF_ONLY" }],
  });
  const rpcRes = await rpcPromise;

  assert.equal(rpcRes.ok, false);
  assert.equal(rpcRes.authorized, false);
  assert.equal(rpcRes.cancelled, true);
  assert.equal("items" in rpcRes, false);
});

test("UT-P1-SyncCoord-InFlight200CancelledOnPreStorageCrossTabDowngrade: aborts 200 OK coordinator commit and evicts mirrored sessionStore when sibling tab downgrades localStore before storage macrotask", async (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  const timers = createManualTimerQueue();
  const rpcGate = createDeferred();
  const staffSerialized = makeSerializedSession({
    email: "staff.a@preservation.example.org",
    role: "staff",
    expOffsetSec: 3600,
  });
  const sharedLocal = createMemoryStorage({ [LS_ADMIN_SESSION_KEY]: staffSerialized });
  const tabSession = createMemoryStorage({ [LS_ADMIN_SESSION_KEY]: staffSerialized });
  let onSuccessCalls = 0;
  const coord = createParcelSyncCoordinator({
    debounceMs: 650,
    scheduleTimer: timers.scheduleTimer,
    cancelTimer: timers.cancelTimer,
    onSuccess: () => {
      onSuccessCalls += 1;
    },
    sendDecisionRpc: async ({ parcelId, expectedUpdatedAt, patch, retryFn }) =>
      await callMonitoringRpc({
        webhookUrl: "https://example.org/exec",
        action: "upsert_parcel_decision",
        body: { parcel_id: parcelId, expected_updated_at: expectedUpdatedAt, ...patch },
        localStore: sharedLocal,
        sessionStore: tabSession,
        retryFn,
        fetchImpl: async () => ({
          ok: true,
          json: async () => await rpcGate.promise,
        }),
      }),
  });
  t.after(() => coord.clearAll());

  coord.enqueue("0420010000077", { screening_notes: "Mirrored Staff edit" }, "");
  const batchPromise = timers.popNextTimer()();
  sharedLocal.setItem(
    LS_ADMIN_SESSION_KEY,
    makeSerializedSession({
      email: "screener@preservation.example.org",
      role: "screener",
      expOffsetSec: 3600,
    })
  );
  rpcGate.resolve({
    ok: true,
    parcel: { parcel_id: "0420010000077" },
    decision: { parcel_id: "0420010000077", decided_at: "2026-11-24T01:00:00.000Z" },
  });
  const batchRes = await batchPromise;

  assert.equal(batchRes.ok, false);
  assert.equal(batchRes.cancelled, true);
  assert.equal(onSuccessCalls, 0);
  assert.equal(tabSession.getItem(LS_ADMIN_SESSION_KEY), null);
  assert.equal(coord.hasPendingOrInFlight("0420010000077"), false);
});

test("UT-P1-AuthClient-ScreenerPullWhitespaceRoleFailsClosed: preserves Screener role and strips staffOnly when server returns whitespace-only role", async (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  const localStore = createMemoryStorage({
    [LS_ADMIN_SESSION_KEY]: makeSerializedSession({
      email: "screener@preservation.example.org",
      role: "screener",
      expOffsetSec: 3600,
    }),
  });

  const resp = await pullCloudMonitoringState({
    webhookUrl: "https://example.org/exec",
    localStore,
    fetchImpl: async () => ({
      ok: true,
      json: async () => ({
        ok: true,
        authorized: true,
        role: "   ",
        parcels: [{ parcel_id: "0152300000001" }],
        staffOnly: { parcelStrategy: [{ strategy_notes: "SECRET" }] },
        pendingModerationCount: 5,
      }),
    }),
  });
  const cached = loadCachedMonitoringState(
    "screener@preservation.example.org",
    "screener",
    { localStore }
  );

  assert.equal(resp.ok, true);
  assert.equal("staffOnly" in resp, false);
  assert.equal("pendingModerationCount" in resp, false);
  assert.equal(cached.role, "screener");
  assert.equal("staffOnly" in cached, false);
});

test("UT-P1-AuthClient-SaveCacheEmptyAndUnknownRoleFailsClosed: defaults empty or unknown role strings to screener in saveCachedMonitoringState", (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  const localStore = createMemoryStorage();

  saveCachedMonitoringState(
    "user@preservation.example.org",
    "",
    {
      staffOnly: { parcelStrategy: [{ strategy_notes: "SECRET_EMPTY" }] },
      pendingModerationCount: 2,
    },
    { localStore }
  );
  const emptyRoleCache = JSON.parse(
    localStore.getItem(`${LS_AUTH_CACHE_PREFIX}user@preservation.example.org`)
  );
  saveCachedMonitoringState(
    "user@preservation.example.org",
    "readonly",
    {
      staffOnly: { parcelStrategy: [{ strategy_notes: "SECRET_UNKNOWN" }] },
      pendingModerationCount: 2,
    },
    { localStore }
  );
  const unknownRoleCache = JSON.parse(
    localStore.getItem(`${LS_AUTH_CACHE_PREFIX}user@preservation.example.org`)
  );

  assert.equal(emptyRoleCache.role, "screener");
  assert.equal("staffOnly" in emptyRoleCache, false);
  assert.equal(unknownRoleCache.role, "screener");
  assert.equal("staffOnly" in unknownRoleCache, false);
});

test("UT-P1-SyncCoord-ReverseMergeWithConcurrentPromotionRemainsAcyclic: keeps committed and promoted lineage cycle-free when reverse B->A commits during uncommitted A->D promotion", async (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  const timers = createManualTimerQueue();
  const batch2Gate = createDeferred();
  let callNum = 0;
  const store = createMemoryStorage({
    [LS_ADMIN_SESSION_KEY]: makeSerializedSession({
      email: "staff.a@preservation.example.org",
      role: "staff",
      expOffsetSec: 3600,
    }),
  });
  const coord = createParcelSyncCoordinator({
    debounceMs: 650,
    scheduleTimer: timers.scheduleTimer,
    cancelTimer: timers.cancelTimer,
    sendDecisionRpc: async ({ parcelId, expectedUpdatedAt, patch, retryFn }) =>
      await callMonitoringRpc({
        webhookUrl: "https://example.org/exec",
        action: "upsert_parcel_decision",
        body: { parcel_id: parcelId, expected_updated_at: expectedUpdatedAt, ...patch },
        localStore: store,
        retryFn,
        fetchImpl: async (_url, init) => {
          callNum += 1;
          const parsed = JSON.parse(init.body);
          if (callNum === 2) {
            await batch2Gate.promise;
          }
          return {
            ok: true,
            json: async () => ({
              ok: true,
              parcel: { parcel_id: parsed.parcel_id },
              decision: {
                parcel_id: parsed.parcel_id,
                decided_at: `2026-11-24T01:10:0${callNum}.000Z`,
              },
            }),
          };
        },
      }),
  });
  t.after(() => coord.clearAll());

  coord.promoteParcelKey("0420010000001", "CAMPUS:LOT_B", "");
  coord.enqueue("CAMPUS:LOT_B", { screening_notes: "Commit 1 A->B" }, "");
  await timers.popNextTimer()();

  coord.promoteParcelKey("CAMPUS:LOT_B", "0420010000001", "");
  coord.enqueue("0420010000001", { screening_notes: "Batch 2 reverse B->A" }, "");
  const batch2Promise = timers.popNextTimer()();

  store.setItem(
    LS_ADMIN_SESSION_KEY,
    makeSerializedSession({
      email: "staff.a@preservation.example.org",
      role: "staff",
      expOffsetSec: -120,
    })
  );
  coord.promoteParcelKey("0420010000001", "CAMPUS:NEW_D", "");
  coord.enqueue("CAMPUS:NEW_D", { screening_notes: "Batch 3 uncommitted A->D" }, "");
  // Batch 3 serializes behind in-flight Batch 2 via getBlockingTicketPromise; resolve Batch 2
  // while store remains expired so Batch 2 commits B->A and unblocked Batch 3 queues for retry.
  const batch3Promise = timers.popNextTimer()();

  batch2Gate.resolve();
  await Promise.all([batch2Promise, batch3Promise]);
  await replayPendingAuthRetries({
    session: { authorized: true, email: "screener@preservation.example.org", role: "screener" },
    localStore: null,
  });

  assert.equal(coord.getPromotedTarget("CAMPUS:LOT_B"), "0420010000001");
  assert.equal(coord.getPromotedTarget("0420010000001"), "0420010000001");
});

test("UT-P1-AuthClient-ExplicitStaffPullDowngradeCancelsCoordinatorBatch: cancels active Staff coordinator batches when explicit-session pull observes server-side Screener downgrade", async (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  const timers = createManualTimerQueue();
  const nowSec = Math.floor(Date.now() / 1000);
  const coord = createParcelSyncCoordinator({
    debounceMs: 650,
    scheduleTimer: timers.scheduleTimer,
    cancelTimer: timers.cancelTimer,
    sendDecisionRpc: async () => ({ ok: true }),
  });
  t.after(() => coord.clearAll());

  coord.enqueue("0420010000081", { screening_notes: "Pending Staff edit" }, "");
  await pullCloudMonitoringState({
    webhookUrl: "https://example.org/exec",
    session: {
      authorized: true,
      email: "user@preservation.example.org",
      role: "staff",
      googleIdToken: makeSyntheticJwt({
        email: "user@preservation.example.org",
        exp: nowSec + 3600,
      }),
    },
    localStore: null,
    fetchImpl: async () => ({
      ok: true,
      json: async () => ({
        ok: true,
        authorized: true,
        role: "screener",
        parcels: [],
      }),
    }),
  });

  assert.equal(timers.scheduled.size, 0);
  assert.equal(coord.hasPendingOrInFlight("0420010000081"), false);
});

test("UT-P1-AuthClient-PurgeForeignPreserveEmailDoesNotInheritActiveStaffRole: evicts foreign staffOnly cache when preserveRole is omitted and another Staff user is active", (t) => {
  clearPendingAuthRetries();
  t.after(() => clearPendingAuthRetries());
  const bobCacheKey = `${LS_AUTH_CACHE_PREFIX}bob@preservation.example.org`;
  const localStore = createMemoryStorage({
    [LS_ADMIN_SESSION_KEY]: makeSerializedSession({
      email: "alice@preservation.example.org",
      role: "staff",
      expOffsetSec: 3600,
    }),
    [bobCacheKey]: '{"role":"staff","staffOnly":{"parcelStrategy":[{"strategy_notes":"BOB_SECRET"}]}}',
  });

  purgePrivateMonitoringStorage({
    localStore,
    preserveEmail: "bob@preservation.example.org",
  });

  assert.equal(localStore.getItem(bobCacheKey), null);
});
