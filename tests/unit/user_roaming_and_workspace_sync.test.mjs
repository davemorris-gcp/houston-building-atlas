import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

import {
  ROAMING_ANON_STORAGE_KEY,
  ROAMING_STORAGE_PREFIX,
  applyBatchEventsReadState,
  applyMarkCaughtUpToRoamingState,
  applyRestoreRoamingSnapshot,
  applyRoamingAuditDecision,
  applyRoamingPrefUpdate,
  applyToggleEventReadState,
  areRoamingStatesEquivalent,
  clearLegacyMirrorOnSignOut,
  compactRoamingStateForTransport,
  createEmptyRoamingState,
  deriveWatermarkInputsFromRoamingState,
  fetchCloudUserRoamingState,
  getRoamingPrefValue,
  isLocalUserRoamingStateDirty,
  loadLocalUserRoamingState,
  mergeUserRoamingStates,
  normalizeRoamingState,
  normalizeStaffEmail,
  pushCloudUserRoamingState,
  saveLocalUserRoamingState,
  syncUserRoamingStateWithCloud,
} from "../../app/js/userRoamingState.js";
import {
  buildMarkCaughtUpState,
  computeWatermarkState,
  filterActivityEvents,
} from "../../app/js/activityLogHelpers.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..", "..");

function createMemoryStorage(initial = {}) {
  const map = new Map(Object.entries(initial));
  return {
    getItem(k) {
      return map.has(k) ? map.get(k) : null;
    },
    setItem(k, v) {
      map.set(String(k), String(v));
    },
    removeItem(k) {
      map.delete(String(k));
    },
    keys() {
      return Array.from(map.keys());
    },
  };
}

function extractAppsScriptCodeFromPython() {
  const cjsPath = path.join(REPO_ROOT, "pipeline", "atlas_pipeline", "apps_script", "Code.cjs");
  assert.ok(fs.existsSync(cjsPath), "Expected standalone Apps Script module at pipeline/atlas_pipeline/apps_script/Code.cjs");
  const pyPath = path.join(REPO_ROOT, "pipeline", "atlas_pipeline", "setup_admin_sheet.py");
  const pySrc = fs.readFileSync(pyPath, "utf8");
  assert.ok(
    pySrc.includes("APPS_SCRIPT_PATH") && pySrc.includes("Code.cjs"),
    "setup_admin_sheet.py must load APPS_SCRIPT_CODE from standalone apps_script/Code.cjs"
  );
  return fs.readFileSync(cjsPath, "utf8");
}

function createAppsScriptVmHarness(appsScriptSource, { passkey = "test-passkey-2026", editors = ["advocate@example.org", "colleague@example.org"] } = {}) {
  const sheets = new Map();
  const cacheStore = new Map();
  const propStore = new Map();

  function makeSheet(name, initialRows = [], hidden = false) {
    const grid = initialRows.map((r) => r.slice());
    let isHidden = hidden;

    function ensureSize(rows, cols) {
      while (grid.length < rows) grid.push([]);
      for (let r = 0; r < rows; r += 1) {
        while (grid[r].length < cols) grid[r].push("");
      }
    }

    function parseA1Range(a1) {
      const s = String(a1).trim();
      if (/^[A-Z]+:[A-Z]+$/i.test(s)) {
        const [c1, c2] = s.toUpperCase().split(":");
        const col1 = c1.charCodeAt(0) - 64;
        const col2 = c2.charCodeAt(0) - 64;
        const maxR = Math.max(1, grid.length);
        return { r1: 1, c1: col1, numR: maxR, numC: col2 - col1 + 1 };
      }
      const m = s.match(/^([A-Z]+)(\d+)$/i);
      if (m) {
        return {
          r1: Number(m[2]),
          c1: m[1].toUpperCase().charCodeAt(0) - 64,
          numR: 1,
          numC: 1,
        };
      }
      throw new Error(`Unsupported A1 notation in fake sheet: ${a1}`);
    }

    function rangeObj(r1, c1, numR = 1, numC = 1) {
      return {
        getValue() {
          return grid[r1 - 1]?.[c1 - 1] ?? "";
        },
        getFormula() {
          const v = String(grid[r1 - 1]?.[c1 - 1] ?? "");
          return v.startsWith("=") ? v : "";
        },
        getValues() {
          const out = [];
          for (let r = 0; r < numR; r += 1) {
            const row = [];
            for (let c = 0; c < numC; c += 1) {
              const rawCell = grid[r1 - 1 + r]?.[c1 - 1 + c] ?? "";
              // Google Sheets strips leading single-quote formula escape when reading getValues()
              row.push(typeof rawCell === "string" && rawCell.startsWith("'") ? rawCell.slice(1) : rawCell);
            }
            out.push(row);
          }
          return out;
        },
        setValues(vals) {
          ensureSize(r1 - 1 + vals.length, c1 - 1 + (vals[0]?.length || 0));
          for (let r = 0; r < vals.length; r += 1) {
            for (let c = 0; c < vals[r].length; c += 1) {
              grid[r1 - 1 + r][c1 - 1 + c] = vals[r][c];
            }
          }
        },
      };
    }

    const sheetApi = {
      _name: name,
      _grid: grid,
      isSheetHidden() {
        return isHidden;
      },
      hideSheet() {
        isHidden = true;
      },
      getDataRange() {
        const numR = grid.length;
        const numC = grid.reduce((mx, r) => Math.max(mx, r.length), 0);
        if (numR === 0 || numC === 0) {
          return { getValues: () => [] };
        }
        return rangeObj(1, 1, numR, numC);
      },
      getRange(arg1, arg2, arg3, arg4) {
        if (typeof arg1 === "string") {
          const p = parseA1Range(arg1);
          return rangeObj(p.r1, p.c1, p.numR, p.numC);
        }
        return rangeObj(arg1, arg2, arg3 || 1, arg4 || 1);
      },
    };
    sheets.set(name, sheetApi);
    return sheetApi;
  }

  // Seed core workbook tabs
  const instrRows = Array.from({ length: 7 }, () => ["", "", ""]);
  instrRows[5][1] = passkey; // Cell B6 (1-indexed row 6, col 2)
  makeSheet("Instructions_&_Apps_Script", instrRows, true);
  makeSheet(
    "Pending_Submissions",
    [
      [
        "status",
        "hcad_num",
        "address",
        "year_built",
        "original_hcad_year",
        "historic_district",
        "contributing",
        "source_type",
        "source_citation",
        "source_url",
        "verified_by",
        "submitted_by",
        "submitted_at",
      ],
    ],
    false
  );
  makeSheet(
    "Approved_Edits",
    [
      ["status", "hcad_num", "address", "year_built"],
      ["Approved", "0621100000014", "1127 KEY ST", "1925"],
    ],
    true
  );

  const ssApi = {
    getOwner: () => ({ getEmail: () => editors[0] || "owner@example.org" }),
    getEditors: () => editors.map((em) => ({ getEmail: () => em })),
    getSheetByName: (name) => sheets.get(name) || null,
    insertSheet: (name) => makeSheet(name, [], false),
  };

  const sandbox = {
    SpreadsheetApp: { getActiveSpreadsheet: () => ssApi },
    CacheService: {
      getScriptCache: () => ({
        get: (k) => (cacheStore.has(k) ? cacheStore.get(k) : null),
        put: (k, v) => {
          cacheStore.set(String(k), String(v));
        },
        remove: (k) => {
          cacheStore.delete(String(k));
        },
      }),
    },
    PropertiesService: {
      getScriptProperties: () => ({
        getProperty: (k) => (propStore.has(k) ? propStore.get(k) : null),
        setProperty: (k, v) => {
          propStore.set(String(k), String(v));
        },
        deleteProperty: (k) => {
          propStore.delete(String(k));
        },
      }),
    },
    LockService: {
      getScriptLock: () => ({
        waitLock: () => {},
        releaseLock: () => {},
      }),
    },
    ContentService: {
      MimeType: { JSON: "application/json", CSV: "text/csv" },
      createTextOutput(txt) {
        let mime = "text/plain";
        return {
          _text: String(txt),
          getMimeType: () => mime,
          getContent: () => String(txt),
          setMimeType(m) {
            mime = m;
            return this;
          },
        };
      },
    },
    UrlFetchApp: {
      fetch: () => ({ getResponseCode: () => 401, getContentText: () => "{}" }),
    },
  };

  vm.createContext(sandbox);
  vm.runInContext(appsScriptSource, sandbox);

  return {
    sandbox,
    sheets,
    cacheStore,
    propStore,
    callDoPost(payloadObj) {
      const out = sandbox.doPost({
        postData: { contents: JSON.stringify(payloadObj) },
      });
      return {
        mimeType: out.getMimeType(),
        data: JSON.parse(out.getContent()),
      };
    },
    callDoGet(params = {}) {
      const out = sandbox.doGet({ parameter: params });
      return {
        mimeType: out.getMimeType(),
        text: out.getContent(),
      };
    },
  };
}

test("UT-ROAM-1: CRDT mergeUserRoamingStates satisfies commutativity, associativity, and idempotence across ties and compaction", () => {
  const stateA = {
    caughtUpAt: "2026-11-20T14:00:00.000Z",
    caughtUpUpdatedAt: 1763647200000,
    readLedger: {
      "ev:shared:tie": { r: 1, t: 5000 },
      "ev:only:a": { r: 1, t: 4000 },
      "ev:lww:winB": { r: 0, t: 3000 },
    },
    prefs: {
      actStream: { v: "external", t: 2000 },
      actGroupBy: { v: "property", t: 3000 },
    },
    auditDecisions: {
      "fid:tie:note": { status: "skipped", at: 7000, note: "Alpha note", suggestedYear: 1924 },
      "fid:only:a": { status: "dismissed", at: 6000, hcad_num: "0621100000014", category: "1927_CUTOFF_TRIAGE" },
    },
  };

  const stateB = {
    caughtUpAt: "2026-11-20T15:00:00.000Z",
    caughtUpUpdatedAt: 1763647200000, // exact tie on caughtUpUpdatedAt, newer caughtUpAt wins
    readLedger: {
      "ev:shared:tie": { r: 0, t: 5000 }, // exact tie on t: explicit Keep Unread (r:0) must win
      "ev:only:b": { r: 0, t: 4500 },
      "ev:lww:winB": { r: 1, t: 6000 }, // newer timestamp marks read
    },
    prefs: {
      actStream: { v: "all", t: 2000 }, // exact tie on t: deterministic tie-breaker
      lastMode: { v: "activity", t: 3500 },
    },
    auditDecisions: {
      "fid:tie:note": { status: "skipped", at: 7000, note: "Beta note", suggestedYear: 1926 },
      "fid:only:b": { status: "accepted", at: 6500 },
    },
  };

  const stateC = {
    caughtUpAt: "2026-11-21T09:30:00.000Z",
    caughtUpUpdatedAt: 1763717400000,
    readLedger: {
      "ev:only:c": { r: 1, t: 8000 },
      "ev:shared:tie": { r: 1, t: 4999 }, // older than 5000 -> must lose to {r:0, t:5000}
    },
    prefs: {
      actGroupBy: { v: "date", t: 9000 },
    },
    auditDecisions: {
      "fid:only:a": { status: "accepted", at: 9500, hcad_num: "0621100000014" },
    },
  };

  // Idempotence: merge(X, X) === normalize(X)
  for (const s of [stateA, stateB, stateC]) {
    assert.deepEqual(mergeUserRoamingStates(s, s), normalizeRoamingState(s));
  }

  // Commutativity: merge(X, Y) === merge(Y, X)
  assert.deepEqual(mergeUserRoamingStates(stateA, stateB), mergeUserRoamingStates(stateB, stateA));
  assert.deepEqual(mergeUserRoamingStates(stateB, stateC), mergeUserRoamingStates(stateC, stateB));
  assert.deepEqual(mergeUserRoamingStates(stateA, stateC), mergeUserRoamingStates(stateC, stateA));

  // Verify Keep Unread tie-breaker survived
  const ab = mergeUserRoamingStates(stateA, stateB);
  assert.equal(ab.readLedger["ev:shared:tie"].r, 0);
  assert.equal(ab.readLedger["ev:lww:winB"].r, 1);

  // Associativity: (A + B) + C === A + (B + C) === (A + C) + B
  const ab_c = mergeUserRoamingStates(mergeUserRoamingStates(stateA, stateB), stateC);
  const a_bc = mergeUserRoamingStates(stateA, mergeUserRoamingStates(stateB, stateC));
  const ac_b = mergeUserRoamingStates(mergeUserRoamingStates(stateA, stateC), stateB);
  assert.deepEqual(ab_c, a_bc);
  assert.deepEqual(ab_c, ac_b);

  // Associativity across >180 readLedger entries with mixed caughtUpAt watermarks
  const makeBulkLedger = (prefix, count, tMs) => {
    const out = {};
    for (let i = 0; i < count; i += 1) {
      out[`${prefix}:${String(i).padStart(3, "0")}`] = { r: 1, t: tMs };
    }
    return out;
  };
  const bulkX = { caughtUpAt: "", caughtUpUpdatedAt: 0, readLedger: makeBulkLedger("x", 110, 4000) };
  const bulkY = { caughtUpAt: "", caughtUpUpdatedAt: 0, readLedger: makeBulkLedger("y", 75, 2000) };
  const bulkZ = {
    caughtUpAt: new Date(5000).toISOString(),
    caughtUpUpdatedAt: 5000,
    readLedger: makeBulkLedger("z", 20, 6000),
  };
  const xz_y = mergeUserRoamingStates(mergeUserRoamingStates(bulkX, bulkZ), bulkY);
  const x_zy = mergeUserRoamingStates(bulkX, mergeUserRoamingStates(bulkZ, bulkY));
  assert.deepEqual(
    xz_y,
    x_zy,
    "mergeUserRoamingStates must remain strictly associative across >60% ledger capacity and watermark transitions"
  );

  // Associativity across >300 entries + cleared Keep Unread pin (`r:0, t:100` -> `r:1, t:200`) + Mark Caught Up
  const pinA = { readLedger: { "wl:pin": { r: 0, t: 100 } } };
  const unpinB = {
    readLedger: {
      "wl:pin": { r: 1, t: 200 },
      ...makeBulkLedger("ev", 299, 500),
    },
  };
  const floodC = { readLedger: makeBulkLedger("ev_late", 10, 600) };
  assert.deepEqual(
    mergeUserRoamingStates(mergeUserRoamingStates(pinA, unpinB), floodC),
    mergeUserRoamingStates(pinA, mergeUserRoamingStates(unpinB, floodC)),
    "mergeUserRoamingStates must remain strictly associative across >300 entries with un-pinned r:0 -> r:1 transitions"
  );
  const afterMarkCaughtUp = applyMarkCaughtUpToRoamingState(unpinB, {
    caughtUpAt: new Date(700).toISOString(),
    nowMs: 700,
  });
  assert.equal(
    mergeUserRoamingStates(pinA, afterMarkCaughtUp).readLedger["wl:pin"]?.r,
    1,
    "Un-pinned Keep Unread item must never resurrect to r:0 after Mark Caught Up"
  );
});

test("UT-ROAM-2 & UT-ROAM-3: caughtUpAt advancement, Undo snapshot restore, Keep Unread pin override, and Property Group batch Mark Read", () => {
  const sampleEvents = [
    {
      id: "permit:coh:0621100000014:26090093",
      stream: "permits",
      severity: "critical",
      adverse: true,
      localDate: "2026-11-20",
      at: "2026-11-20T14:00:00.000Z",
      hcad: "0621100000014",
      title: "Demolition permit application",
    },
    {
      id: "wl:auto:prop-1:chk-14:1",
      stream: "watchlist",
      severity: "warning",
      adverse: true,
      localDate: "2026-11-20",
      at: "2026-11-20T15:00:00.000Z",
      hcad: "0621100000014",
      title: "Sold permit hit on watched parcel",
    },
    {
      id: "agenda:item:2026-12-03:2026-1524:added",
      stream: "agenda",
      severity: "warning",
      adverse: false,
      localDate: "2026-11-21",
      at: "2026-11-21T12:00:00.000Z",
      hcad: "0040570000008",
      title: "Liberty Green replat filing",
    },
  ];
  const nowIso = "2026-11-22T01:00:00.000Z";
  const tBase = Date.parse("2026-11-22T01:00:00.000Z");

  // 1. Property Group batch Mark Read (N) marks only the parcel's signals read
  let state = createEmptyRoamingState();
  state = applyBatchEventsReadState(
    state,
    ["permit:coh:0621100000014:26090093", "wl:auto:prop-1:chk-14:1"],
    true,
    tBase + 100
  );
  let wmInputs = deriveWatermarkInputsFromRoamingState(state);
  let wm = computeWatermarkState({
    events: sampleEvents,
    caughtUpAt: wmInputs.caughtUpAt,
    readEventIds: wmInputs.readEventIds,
    unreadEventIds: wmInputs.unreadEventIds,
    nowIso,
  });
  assert.equal(wm.isImplicitFirstVisit, true);
  assert.equal(wm.unreadCount, 1);
  assert.equal(wm.unreadIds.has("agenda:item:2026-12-03:2026-1524:added"), true);

  // 2. Pin 1 read item back to Keep Unread (○ Keep Unread -> r: 0) BEFORE clicking Mark Caught Up
  state = applyToggleEventReadState(
    state,
    "permit:coh:0621100000014:26090093",
    false,
    tBase + 200
  );
  const preCaughtUpSnapshot = JSON.parse(JSON.stringify(state));

  // 3. Click Mark Caught Up -> advances caughtUpAt, clears unpinned agenda item, PRESERVES Keep Unread pin!
  const nextWmCalc = buildMarkCaughtUpState({
    events: sampleEvents,
    prevReadEventIds: wmInputs.readEventIds,
    nowIso,
  });
  const caughtUpState = applyMarkCaughtUpToRoamingState(state, {
    caughtUpAt: nextWmCalc.caughtUpAt,
    readEventIds: nextWmCalc.readEventIds,
    nowMs: tBase + 500,
  });

  wmInputs = deriveWatermarkInputsFromRoamingState(caughtUpState);
  assert.equal(wmInputs.caughtUpAt, "2026-11-22T01:00:00.000Z");
  assert.ok(
    wmInputs.unreadEventIds.includes("permit:coh:0621100000014:26090093"),
    "Explicit Keep Unread pin must survive Mark Caught Up"
  );
  wm = computeWatermarkState({
    events: sampleEvents,
    caughtUpAt: wmInputs.caughtUpAt,
    readEventIds: wmInputs.readEventIds,
    unreadEventIds: wmInputs.unreadEventIds,
    nowIso,
  });
  assert.equal(wm.isImplicitFirstVisit, false);
  assert.equal(wm.unreadCount, 1, "Only the 1 pinned Keep Unread item should remain unread");
  assert.equal(wm.unreadIds.has("permit:coh:0621100000014:26090093"), true);
  assert.equal(wm.unreadAdverseCount, 1);

  // Direct boundary check: unreadEventIds must override BOTH readEventIds AND event.at <= caughtUpAt
  const directConflictWm = computeWatermarkState({
    events: sampleEvents,
    caughtUpAt: "2026-11-22T01:00:00.000Z",
    readEventIds: ["permit:coh:0621100000014:26090093"],
    unreadEventIds: ["permit:coh:0621100000014:26090093"],
    nowIso,
  });
  assert.equal(
    directConflictWm.unreadIds.has("permit:coh:0621100000014:26090093"),
    true,
    "unreadEventIds must take strict precedence when an ID appears in both readEventIds and unreadEventIds"
  );

  const sinceLastVisit = filterActivityEvents({
    events: sampleEvents,
    watermarkState: wm,
    rangeMode: "since_last_visit",
    nowIso,
  });
  assert.deepEqual(
    sinceLastVisit.map((e) => e.id),
    ["permit:coh:0621100000014:26090093"]
  );

  // 4. Undo (applyRestoreRoamingSnapshot) restores prior state AND beats caughtUpState on cloud merge
  const undoneState = applyRestoreRoamingSnapshot(preCaughtUpSnapshot, tBase + 900, caughtUpState);
  const mergedAfterUndo = mergeUserRoamingStates(caughtUpState, undoneState);
  assert.equal(mergedAfterUndo.caughtUpAt, "");
  const undoneInputs = deriveWatermarkInputsFromRoamingState(mergedAfterUndo);
  const undoneWm = computeWatermarkState({
    events: sampleEvents,
    caughtUpAt: undoneInputs.caughtUpAt,
    readEventIds: undoneInputs.readEventIds,
    unreadEventIds: undoneInputs.unreadEventIds,
    nowIso,
  });
  assert.equal(undoneWm.isImplicitFirstVisit, true);
  assert.equal(undoneWm.unreadCount, 2);
  assert.equal(undoneWm.unreadIds.has("permit:coh:0621100000014:26090093"), true);
  assert.equal(undoneWm.unreadIds.has("agenda:item:2026-12-03:2026-1524:added"), true);

  // 5. Explicitly clicking Mark Read on the pinned item clears the Keep Unread pin
  const clearedPinState = applyToggleEventReadState(
    caughtUpState,
    "permit:coh:0621100000014:26090093",
    true,
    tBase + 1500
  );
  const clearedInputs = deriveWatermarkInputsFromRoamingState(clearedPinState);
  const clearedWm = computeWatermarkState({
    events: sampleEvents,
    caughtUpAt: clearedInputs.caughtUpAt,
    readEventIds: clearedInputs.readEventIds,
    unreadEventIds: clearedInputs.unreadEventIds,
    nowIso,
  });
  assert.equal(clearedWm.unreadCount, 0);
});

test("UT-ROAM-4: Multi-user sign-out and shared-browser account switch isolate watermarks, prefs, reviewer, and audit decisions", () => {
  const storage = createMemoryStorage();
  const userA = "advocate@example.org";
  const userB = "colleague@example.org";

  assert.equal(normalizeStaffEmail("  Advocate@Example.org "), userA);
  assert.equal(normalizeStaffEmail("not-an-email"), "");

  // User A configures preferences, watermark, Keep Unread pin, and #audit=1 decision
  let stateA = createEmptyRoamingState();
  stateA = applyMarkCaughtUpToRoamingState(stateA, {
    caughtUpAt: "2026-11-22T00:30:00.000Z",
    readEventIds: ["ev:a1", "ev:a2"],
    nowMs: 10000,
  });
  stateA = applyToggleEventReadState(stateA, "ev:pinA", false, 11000);
  stateA = applyRoamingPrefUpdate(stateA, "actStream", "all", 12000);
  stateA = applyRoamingPrefUpdate(stateA, "actGroupBy", "date", 12000);
  stateA = applyRoamingPrefUpdate(stateA, "lastMode", "watchlist", 12000);
  stateA = applyRoamingPrefUpdate(stateA, "reviewerName", userA, 12000);
  stateA = applyRoamingPrefUpdate(stateA, "inspectorWide", true, 12000);
  stateA = applyRoamingPrefUpdate(stateA, "ecosystemTheme", "dark", 12000);
  stateA = applyRoamingAuditDecision(
    stateA,
    "fid:0621100000014",
    { status: "skipped", note: "Check Sanborn Vol 6" },
    13000
  );

  saveLocalUserRoamingState(stateA, { email: userA, storage, markDirty: false, mirrorLegacy: true });
  // Simulate plat-monitor writing phpam:reviewer during User A session
  storage.setItem("phpam:reviewer", JSON.stringify(userA));

  // Case 1: Direct account switch to User B even before sign-out must NOT leak User A's mirrored keys
  const directUserB = loadLocalUserRoamingState({ email: userB, storage });
  assert.equal(directUserB.caughtUpAt, "");
  assert.deepEqual(directUserB.readLedger, {});
  assert.deepEqual(directUserB.prefs, {});
  assert.deepEqual(directUserB.auditDecisions, {});

  // Case 2: User A clicks Sign Out -> clearLegacyMirrorOnSignOut scrubs all shared legacy keys
  clearLegacyMirrorOnSignOut(storage);
  assert.equal(JSON.parse(storage.getItem("phpam:caughtUpAt")), "");
  assert.deepEqual(JSON.parse(storage.getItem("phpam:readEventIds")), []);
  assert.equal(storage.getItem("phpam:actStream"), null);
  assert.equal(storage.getItem("phpam:actGroupBy"), null);
  assert.equal(storage.getItem("phpam:lastMode"), null);
  assert.equal(storage.getItem("phpam:reviewer"), null);
  assert.equal(storage.getItem("atlas_inspector_wide"), null);
  assert.equal(storage.getItem("hba:ecosystem:theme"), null);
  assert.deepEqual(JSON.parse(storage.getItem("ph_atlas_audit_decisions_v1") || "{}"), {});

  // User B loads clean state after sign-out while User A's namespaced profile remains preserved for offline re-login
  const cleanUserB = loadLocalUserRoamingState({ email: userB, storage });
  assert.ok(areRoamingStatesEquivalent(cleanUserB, createEmptyRoamingState()));

  const reloadedUserA = loadLocalUserRoamingState({ email: userA, storage });
  assert.equal(reloadedUserA.caughtUpAt, "2026-11-22T00:30:00.000Z");
  assert.equal(reloadedUserA.readLedger["ev:pinA"]?.r, 0);
  assert.equal(getRoamingPrefValue(reloadedUserA, "actStream"), "all");
  assert.ok(storage.getItem(`${ROAMING_STORAGE_PREFIX}${userA}`));
});

test("UT-ROAM-5: CSV-stub, HTML login wall, HTTP error, and offline fault tolerance preserve dirty queue until cloud recovers", async () => {
  const storage = createMemoryStorage();
  const session = {
    authorized: true,
    verifiedByWebhook: true,
    email: "advocate@example.org",
    adminPasskey: "test-passkey-2026",
  };
  const webhookUrl = "https://script.google.com/macros/s/test-deployment/exec";

  // Pre-sign-in anonymous activity
  const anonDraft = applyToggleEventReadState(createEmptyRoamingState(), "ev:anon:1", true, 5000);
  storage.setItem(ROAMING_ANON_STORAGE_KEY, JSON.stringify(anonDraft));

  // Save dirty authenticated mutation while offline
  const localDirty = applyRoamingPrefUpdate(
    applyToggleEventReadState(createEmptyRoamingState(), "ev:offline:pin", false, 6000),
    "actGroupBy",
    "date",
    6100
  );
  saveLocalUserRoamingState(localDirty, {
    email: session.email,
    storage,
    markDirty: true,
    mirrorLegacy: true,
  });
  assert.equal(isLocalUserRoamingStateDirty({ email: session.email, storage }), true);

  // Stubs: E2E CSV stub ("hcad_num,status\n"), HTML page, HTTP 503, and network error
  const stubFetches = [
    async () => ({ ok: true, status: 200, text: async () => "hcad_num,status\n" }),
    async () => ({ ok: true, status: 200, text: async () => "<html><body>Sign in</body></html>" }),
    async () => ({ ok: false, status: 503, text: async () => "Service Unavailable" }),
    async () => {
      throw new Error("net::ERR_INTERNET_DISCONNECTED");
    },
  ];

  for (const stubFn of stubFetches) {
    const getRes = await fetchCloudUserRoamingState({ webhookUrl, session, fetchImpl: stubFn });
    assert.equal(getRes.ok, false);
    assert.equal(getRes.offlineOrStub, true);

    const putRes = await pushCloudUserRoamingState({
      webhookUrl,
      session,
      state: localDirty,
      fetchImpl: stubFn,
    });
    assert.equal(putRes.ok, false);
    assert.equal(putRes.offlineOrStub, true);

    const syncRes = await syncUserRoamingStateWithCloud({
      webhookUrl,
      session,
      storage,
      fetchImpl: stubFn,
      includeAnonState: true,
    });
    assert.equal(syncRes.ok, false);
    assert.equal(syncRes.offlineOrStub, true);
    assert.equal(syncRes.state.readLedger["ev:offline:pin"]?.r, 0);
    assert.equal(syncRes.state.readLedger["ev:anon:1"]?.r, 1);
    assert.equal(isLocalUserRoamingStateDirty({ email: session.email, storage }), true);
  }

  // Once cloud recovers, syncUserRoamingStateWithCloud merges remote + local, pushes, and clears dirty flag
  let pushedPayload = null;
  const liveFetch = async (_url, opts) => {
    const body = JSON.parse(opts.body);
    if (body.action === "get_user_state") {
      return {
        ok: true,
        status: 200,
        text: async () =>
          JSON.stringify({
            ok: true,
            authorized: true,
            email: session.email,
            userState: {
              caughtUpAt: "2026-11-21T12:00:00.000Z",
              caughtUpUpdatedAt: 4000,
              readLedger: { "ev:remote:1": { r: 1, t: 4500 } },
              prefs: { actStream: { v: "all", t: 7000 } },
              auditDecisions: {},
            },
          }),
      };
    }
    if (body.action === "put_user_state") {
      pushedPayload = body.user_state;
      return {
        ok: true,
        status: 200,
        text: async () =>
          JSON.stringify({
            ok: true,
            authorized: true,
            email: session.email,
            userState: body.user_state,
          }),
      };
    }
    throw new Error(`Unexpected action ${body.action}`);
  };

  const recovered = await syncUserRoamingStateWithCloud({
    webhookUrl,
    session,
    storage,
    fetchImpl: liveFetch,
    includeAnonState: true,
  });
  assert.equal(recovered.ok, true);
  assert.equal(recovered.pushed, true);
  assert.ok(pushedPayload);
  assert.equal(recovered.state.readLedger["ev:offline:pin"]?.r, 0);
  assert.equal(recovered.state.readLedger["ev:anon:1"]?.r, 1);
  assert.equal(recovered.state.readLedger["ev:remote:1"]?.r, 1);
  assert.equal(getRoamingPrefValue(recovered.state, "actStream"), "all");
  assert.equal(getRoamingPrefValue(recovered.state, "actGroupBy"), "date");
  assert.equal(isLocalUserRoamingStateDirty({ email: session.email, storage }), false);

  // Also verify compactRoamingStateForTransport caps oversized ledgers cleanly below 42,000 chars
  // using monotonic timestamp-first LWW ordering (newest timestamps survive; equal-timestamp r:0 beats r:1).
  const hugeLedger = {};
  for (let i = 0; i < 450; i += 1) {
    hugeLedger[`permit:coh:0621100000014:project_${String(i).padStart(4, "0")}_${"x".repeat(80)}`] = {
      r: i >= 445 ? 0 : 1,
      t: i >= 444 ? 2000 : 1000 + i,
    };
  }
  const compacted = compactRoamingStateForTransport({ readLedger: hugeLedger }, 12000);
  assert.ok(JSON.stringify(compacted).length <= 12000);
  assert.equal(compacted.readLedger[`permit:coh:0621100000014:project_0449_${"x".repeat(80)}`]?.r, 0);
});

test("UT-ROAM-6: APPS_SCRIPT_CODE in Node vm enforces zero double quotes, 1-RTT verify_admin, server CRDT merge, Plat_&_Watchlist_Sync key upsert, and ?feed=plat_sync CSV", () => {
  const appsScript = extractAppsScriptCodeFromPython();
  assert.equal(
    appsScript.includes('"'),
    false,
    "APPS_SCRIPT_CODE must never contain literal double-quote characters"
  );

  const vmApp = createAppsScriptVmHarness(appsScript, {
    passkey: "test-passkey-2026",
    editors: ["advocate@example.org", "colleague@example.org"],
  });

  // 1. Unauthorized verify_admin & get_user_state rejected
  const badAuth = vmApp.callDoPost({
    action: "verify_admin",
    admin_email: "intruder@example.org",
    admin_passkey: "test-passkey-2026",
  });
  assert.equal(badAuth.data.authorized, false);

  const badGet = vmApp.callDoPost({
    action: "get_user_state",
    admin_email: "advocate@example.org",
    admin_passkey: "wrong-passkey",
  });
  assert.equal(badGet.data.ok, false);
  assert.equal(badGet.data.authorized, false);

  // 2. Initial 1-round-trip verify_admin returns supportsRoamingSync: true and userState: null
  const initVerify = vmApp.callDoPost({
    action: "verify_admin",
    admin_email: "Advocate@Example.org",
    admin_passkey: "test-passkey-2026",
  });
  assert.equal(initVerify.data.authorized, true);
  assert.equal(initVerify.data.supportsRoamingSync, true);
  assert.equal(initVerify.data.email, "advocate@example.org");
  assert.equal(initVerify.data.userState, null);

  // 3. put_user_state from Work Computer + concurrent put_user_state from Home Computer merge commutatively
  const putWork = vmApp.callDoPost({
    action: "put_user_state",
    admin_email: "advocate@example.org",
    admin_passkey: "test-passkey-2026",
    user_state: {
      caughtUpAt: "2026-11-22T01:00:00.000Z",
      caughtUpUpdatedAt: 20000,
      readLedger: {
        "ev:keep:unread": { r: 0, t: 15000 }, // pinned BEFORE caughtUpAt -> must still survive on server!
        "ev:work:read": { r: 1, t: 19000 },
      },
      prefs: { actStream: { v: "external", t: 18000 } },
      auditDecisions: {
        "fid:1": {
          status: "skipped",
          at: 17000,
          note: "Verify deed",
          hcad_num: "0621100000014",
          category: "1927_CUTOFF_TRIAGE",
          suggestedYear: 1925,
        },
      },
    },
  });
  assert.equal(putWork.data.ok, true);
  assert.equal(putWork.data.userState.readLedger["ev:keep:unread"]?.r, 0);
  assert.equal(putWork.data.userState.auditDecisions["fid:1"]?.hcad_num, "0621100000014");
  assert.equal(putWork.data.userState.auditDecisions["fid:1"]?.suggestedYear, 1925);

  const putHome = vmApp.callDoPost({
    action: "put_user_state",
    admin_email: "advocate@example.org",
    admin_passkey: "test-passkey-2026",
    user_state: {
      caughtUpAt: "2026-11-21T20:00:00.000Z",
      caughtUpUpdatedAt: 10000, // older watermark than Work Computer -> Work watermark wins
      readLedger: {
        "ev:home:read": { r: 1, t: 21000 },
      },
      prefs: { actStream: { v: "all", t: 22000 }, actGroupBy: { v: "date", t: 22000 } },
      auditDecisions: {},
    },
  });
  assert.equal(putHome.data.ok, true);
  assert.equal(putHome.data.userState.caughtUpAt, "2026-11-22T01:00:00.000Z");
  assert.equal(putHome.data.userState.readLedger["ev:keep:unread"]?.r, 0);
  assert.equal(putHome.data.userState.readLedger["ev:work:read"]?.r, 1);
  assert.equal(putHome.data.userState.readLedger["ev:home:read"]?.r, 1);
  assert.equal(putHome.data.userState.prefs.actStream.v, "all");
  assert.equal(putHome.data.userState.prefs.actGroupBy.v, "date");
  assert.equal(putHome.data.userState.auditDecisions["fid:1"]?.status, "skipped");

  // Hidden _Staff_User_State sheet has exactly 1 header + 1 user row (upserted in place, never duplicated)
  const userSheet = vmApp.sheets.get("_Staff_User_State");
  assert.ok(userSheet, "Expected _Staff_User_State sheet to be auto-created");
  assert.equal(userSheet.isSheetHidden(), true);
  assert.equal(userSheet._grid.length, 2, "Expected header + 1 upserted row for advocate@example.org");

  // Evict CacheService to verify fallback read from PropertiesService + _Staff_User_State via 1-RTT verify_admin
  vmApp.cacheStore.clear();
  const verifyHydrated = vmApp.callDoPost({
    action: "verify_admin",
    admin_email: "advocate@example.org",
    admin_passkey: "test-passkey-2026",
  });
  assert.equal(verifyHydrated.data.supportsRoamingSync, true);
  assert.equal(verifyHydrated.data.userState.readLedger["ev:keep:unread"]?.r, 0);
  assert.equal(verifyHydrated.data.userState.prefs.actGroupBy.v, "date");

  // 4. Phase 1 retirement of unauthenticated ?feed=plat_sync + PLAT_SYNC_CSV_V1 cache eviction + normalized parcel/dossier upserts
  vmApp.cacheStore.set("PLAT_SYNC_CSV_V1", "stale,private,csv");
  const retiredPlatFeed = vmApp.callDoGet({ feed: "plat_sync" });
  assert.equal(retiredPlatFeed.mimeType, "application/json");
  const retiredBody = JSON.parse(retiredPlatFeed.text);
  assert.equal(retiredBody.ok, false);
  assert.equal(retiredBody.authorized, false);
  assert.equal(
    vmApp.cacheStore.has("PLAT_SYNC_CSV_V1"),
    false,
    "doGet must evict legacy PLAT_SYNC_CSV_V1 from CacheService"
  );

  const parcelDec = vmApp.callDoPost({
    action: "upsert_parcel_decision",
    admin_email: "advocate@example.org",
    admin_passkey: "test-passkey-2026",
    parcel_id: "0621100000014",
    patch: {
      parcel_id: "0621100000014",
      hcad_num: "0621100000014",
      primary_address: "1127 KEY ST",
      year_built: 1924,
      review_status: "assessed",
      significance_level: "high",
      threat_stage: 3,
      advocacy_status: "active_advocacy",
      screening_notes: "Pre-1930 bungalow verified.",
    },
  });
  assert.equal(parcelDec.data.ok, true);
  assert.equal(parcelDec.data.parcel.attention_level, "act");

  const watchUpsert = vmApp.callDoPost({
    action: "upsert_watchlist_dossier",
    admin_email: "advocate@example.org",
    admin_passkey: "test-passkey-2026",
    watch_property_id: "prop-kirby-mansion",
    dossier: {
      id: "prop-kirby-mansion",
      parcelId: "0152300000001",
      hcadAccount: "0152300000001",
      address: "2000 Smith St.",
      propertyName: "Kirby Mansion",
      attentionLevel: "record",
      advocacyStatus: "closed",
      outcome: "lost",
      updatedAt: "2026-11-22T00:30:00Z",
    },
  });
  assert.equal(watchUpsert.data.ok, true);

  const pendingSheet = vmApp.sheets.get("Pending_Submissions");
  assert.equal(
    pendingSheet._grid.length,
    1,
    "Normalized parcel decisions and Watchlist dossiers must NEVER append to Pending_Submissions"
  );

  const defaultApprovedCsv = vmApp.callDoGet({});
  assert.match(defaultApprovedCsv.text, /0621100000014,1127 KEY ST,1925/);
  assert.doesNotMatch(defaultApprovedCsv.text, /PLAT_REV:/);

  // 5. Differential equivalence between client mergeUserRoamingStates and server mergeUserRoamingStateServer_
  const diffA = {
    caughtUpAt: "2026-11-22T01:00:00.000Z",
    caughtUpUpdatedAt: 30000,
    readEventIds: ["legacy:read:1"],
    unreadEventIds: ["legacy:unread:1"],
    readLedger: { "ev:tie": { r: -1, t: 25000 }, "ev:undo": { r: -1, t: 31000 } },
    prefs: {
      actStream: { v: "external", t: 20000 },
      unknownCustomKey: { v: { nested: "should-be-stripped" }, t: 99000 },
    },
    auditDecisions: { "fid:eq": { status: "skipped", at: 40000, note: "A" } },
  };
  const diffB = {
    caughtUpAt: "2026-11-22T02:00:00.000Z",
    caughtUpUpdatedAt: 30000,
    readLedger: { "ev:tie": { r: 0, t: 25000 }, "ev:undo": { r: 1, t: 29000 } },
    prefs: {
      actStream: { v: "all", t: 20000 },
      inspectorWide: true,
    },
    auditDecisions: { "fid:eq": { status: "skipped", at: 40000, note: "B" } },
  };
  const clientMerged = mergeUserRoamingStates(diffA, diffB);
  const serverMerged = JSON.parse(
    JSON.stringify(vmApp.sandbox.mergeUserRoamingStateServer_(diffA, diffB))
  );
  assert.deepEqual(
    serverMerged,
    clientMerged,
    "Apps Script mergeUserRoamingStateServer_ must be observationally equivalent to client mergeUserRoamingStates"
  );
  assert.equal("unknownCustomKey" in serverMerged.prefs, false);
});

test("UT-ROAM-7: Cross-module curatedEdits authenticateAdminSession 1-RTT hydration, staffWorkbench saveAuditDecision sync, and clearAdminSession isolation", async () => {
  const { authenticateAdminSession, clearAdminSession } = await import(
    "../../app/js/curatedEdits.js"
  );
  const {
    saveAuditDecision,
    loadAuditDecisions,
    flushPendingRoamingAuditSync,
  } = await import("../../app/js/staffWorkbench.js");

  const prevLocal = globalThis.localStorage;
  const prevSession = globalThis.sessionStorage;
  const prevFetch = globalThis.fetch;
  const fakeLocal = createMemoryStorage();
  const fakeSession = createMemoryStorage();
  globalThis.localStorage = fakeLocal;
  globalThis.sessionStorage = fakeSession;

  try {
    let pushedRoamingPayload = null;
    globalThis.fetch = async (_url, opts) => {
      const body = JSON.parse(opts.body || "{}");
      if (body.action === "verify_admin") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            authorized: true,
            supportsRoamingSync: true,
            email: "advocate@example.org",
            userState: {
              caughtUpAt: "2026-11-22T00:45:00.000Z",
              caughtUpUpdatedAt: 50000,
              readLedger: { "ev:cloud:pin": { r: 0, t: 48000 } },
              prefs: { inspectorWide: { v: true, t: 49000 }, ecosystemTheme: { v: "dark", t: 49000 } },
              auditDecisions: {
                "dir_pre:0621100000014": {
                  status: "skipped",
                  at: 55000,
                  note: "Synced from office desktop",
                },
              },
            },
          }),
        };
      }
      if (body.action === "put_user_state") {
        pushedRoamingPayload = body.user_state;
        return {
          ok: true,
          status: 200,
          text: async () =>
            JSON.stringify({
              ok: true,
              authorized: true,
              email: "advocate@example.org",
              userState: body.user_state,
            }),
        };
      }
      throw new Error(`Unexpected fetch action: ${body.action}`);
    };

    // 1. 1-RTT authenticateAdminSession hydrates cloud userState directly into localStorage & legacy mirrors
    const authRes = await authenticateAdminSession({
      adminEmail: "advocate@example.org",
      adminPasskey: "test-passkey-2026",
      webhookUrl: "https://script.google.com/macros/s/test/exec",
    });
    assert.equal(authRes.ok, true);
    assert.equal(fakeLocal.getItem("atlas_inspector_wide"), "1");
    assert.equal(fakeLocal.getItem("hba:ecosystem:theme"), "dark");

    const decisionsAfterLogin = loadAuditDecisions();
    assert.equal(decisionsAfterLogin["dir_pre:0621100000014"]?.status, "skipped");

    // 2. Saving a new rapid-audit decision in staffWorkbench writes into namespaced roaming state
    saveAuditDecision("dir_abs:0610870390001", "dismissed", "Verified intact on 1924 Sanborn");
    const hydratedLocal = loadLocalUserRoamingState({
      email: "advocate@example.org",
      storage: fakeLocal,
    });
    assert.equal(hydratedLocal.auditDecisions["dir_abs:0610870390001"]?.status, "dismissed");
    assert.equal(hydratedLocal.readLedger["ev:cloud:pin"]?.r, 0);

    // Flush pending workbench cloud push deterministically (zero wall-clock sleep)
    await flushPendingRoamingAuditSync();
    assert.ok(pushedRoamingPayload, "Expected pushCloudUserRoamingState from saveAuditDecision");
    assert.equal(
      pushedRoamingPayload.auditDecisions["dir_abs:0610870390001"]?.status,
      "dismissed"
    );

    // 3. clearAdminSession scrubs shared legacy mirrors AND in-memory _auditMemoryFallback synchronously
    clearAdminSession();
    assert.equal(fakeLocal.getItem("atlas_inspector_wide"), null);
    assert.equal(fakeLocal.getItem("hba:ecosystem:theme"), null);
    assert.equal(fakeLocal.getItem("ph_atlas_audit_decisions_v1"), null);
    assert.deepEqual(loadAuditDecisions(), {});

    // 4. First-time login for User B (`userState: null`) with pre-sign-in local `#audit=1` triage pushes seed to cloud
    pushedRoamingPayload = null;
    saveAuditDecision("fid:prelogin:b", "skipped", "Triaged before signing in");
    globalThis.fetch = async (_url, opts) => {
      const body = JSON.parse(opts.body || "{}");
      if (body.action === "verify_admin") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            authorized: true,
            supportsRoamingSync: true,
            email: "colleague@example.org",
            userState: null,
          }),
        };
      }
      if (body.action === "put_user_state") {
        pushedRoamingPayload = body.user_state;
        return {
          ok: true,
          status: 200,
          text: async () => JSON.stringify({ ok: true, authorized: true, email: "colleague@example.org" }),
        };
      }
      throw new Error(`Unexpected fetch action: ${body.action}`);
    };
    const authB = await authenticateAdminSession({
      adminEmail: "colleague@example.org",
      adminPasskey: "test-passkey-2026",
      webhookUrl: "https://script.google.com/macros/s/test/exec",
    });
    assert.equal(authB.ok, true);
    await new Promise((r) => setImmediate(r));
    assert.equal(pushedRoamingPayload?.auditDecisions["fid:prelogin:b"]?.status, "skipped");
    assert.equal("dir_abs:0610870390001" in (pushedRoamingPayload?.auditDecisions || {}), false);
    clearAdminSession();
  } finally {
    if (prevLocal === undefined) delete globalThis.localStorage;
    else globalThis.localStorage = prevLocal;
    if (prevSession === undefined) delete globalThis.sessionStorage;
    else globalThis.sessionStorage = prevSession;
    if (prevFetch === undefined) delete globalThis.fetch;
    else globalThis.fetch = prevFetch;
  }
});
