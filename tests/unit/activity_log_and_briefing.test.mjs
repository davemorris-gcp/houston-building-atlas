import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  MAX_STORED_READ_EVENT_IDS,
  buildAgendaCycleEventId,
  buildAgendaItemEventId,
  buildAutoCheckEventId,
  buildEntityUnreadIndex,
  buildEventAtlasMapHref,
  buildHahcCycleEventId,
  buildHahcFilingEventId,
  buildHahcGuidelineEventId,
  buildManualCheckEventId,
  buildMarkCaughtUpState,
  buildPermitCohEventId,
  buildPermitDshsEventId,
  buildStaffPropEventId,
  buildStaffReviewEventId,
  buildSweepDegradedEventId,
  buildWatchPropertyFromPermitAlert,
  buildWatchStatusEventId,
  collapseUpper,
  compactDailyRunsForLedger,
  computeWatermarkState,
  filterActivityEvents,
  findMatchingWatchPropertyForAlert,
  fnv1a32Hex,
  formatActivityBriefingText,
  groupActivityEventsByDate,
  isNotableNewCycleItem,
  normalizeHcad13,
  resolveInitialMonitorMode,
  sanitizeActivityEventForRender,
  sanitizeActivityText,
  slugToken,
  synthesizeLiveMonitorEvents,
  toChicagoDateIso,
} from "../../app/js/activityLogHelpers.js";
import {
  findChecklistRow,
  normalizeWatchProperty,
  reconcileWatchlistProperty,
} from "../../app/js/watchlistSyncHelpers.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..", "..");

console.log("Running Activity Log & Morning Briefing tests (UT-4..UT-7)...");

// ---------------------------------------------------------------------------
// UT-4: Cross-Language Python <-> Node Canonical Event ID & Schema Parity
// ---------------------------------------------------------------------------
{
  const sharedVectors = {
    texts: [
      "  [AUTO-SWEEP] Café Houston \u2014 Phase 2\n  Permit #26090093  ",
      "Special Warranty Deed (#RP-2026-399473) & $7M loan\u2013recorded 10/1/2026",
      "",
    ],
    hcads: [
      "0152300000001",
      "152300000001", // 12 digits unpadded
      "41017002015", // 11 digits unpadded
      "1234567890", // 10 digits unpadded
      "#066-045-013-0001, 0660450130016",
      "1.523E+11",
      "",
    ],
    autoChecks: [
      {
        propId: "prop-kirby-mansion",
        rowId: "CHK-10",
        chk: { agencyRecordId: "TABS2026022839", result: "[AUTO-SWEEP] TDLR registration" },
      },
      {
        propId: "prop-kirby-mansion",
        rowId: "chk-04",
        chk: { result: "  [auto-sweep]  Smith St C Store LLC \u2014 Active Sales Tax  " },
      },
    ],
    manualChecks: [
      {
        propId: "prop-kirby-mansion",
        rowId: "CHK-02",
        chk: { date: "2026-10-14T15:00:00Z", result: "Called  Greenberg & Co.\u2014left message" },
      },
    ],
    watchStatuses: [
      { propId: "prop-kirby-mansion", rowId: "CHK-14", status: "Flagged", dateIso: "2026-10-29" },
    ],
    staffProps: [
      { propId: "prop-kirby-mansion", action: "added", dateIso: "" },
      { propId: "plat-2026-1585", action: "Archived", dateIso: "" },
      { propId: "prop-kirby-mansion", action: "edited", dateIso: "2026-10-29" },
    ],
    staffReviews: [{ cycleKey: "2026-10-15", appNo: "2026-1585", tier: "1" }],
    agendaCycles: ["2026-10-29"],
    agendaItems: [
      { cycleKey: "2026-10-29", appNo: "2026-1585", kind: "added" },
      { cycleKey: "2026-10-29", appNo: "DPV-26073533", kind: "dpv" },
      { cycleKey: "2026-10-29", appNo: "2026-1585", kind: "notice_pdf" },
      { cycleKey: "2026-10-29", appNo: "2026-1585", kind: "plat_pdf" },
      {
        cycleKey: "2026-10-29",
        appNo: "2026-1585",
        kind: `deferral:${slugToken("1st Request (2 weeks)")}`,
      },
    ],
    permitsCoh: [
      { hcadRaw: "152300000001", projectNo: "26090093", fallbackDesc: "", dateIso: "2026-10-14" },
      { hcadRaw: "410170020150", projectNo: "", fallbackDesc: "BLD DEMO / SEWER", dateIso: "2026-10-28" },
    ],
    permitsDshs: [
      { hcadRaw: "0152300000001", notifNo: "2026005825", fallbackAddr: "", dateIso: "2026-10-13" },
      { hcadRaw: "", notifNo: "", fallbackAddr: "2000 Smith St.", dateIso: "2026-10-13" },
    ],
    hahcCycles: ["2026-10-08"],
    hahcGuidelines: [
      { meetingDate: "2026-10-08", titleOrUrl: "Norhill Historic District Design Guidelines \u2014 Redline" },
    ],
    hahcFilings: [
      { filingId: "HP2026_0301", outcome: "" },
      { filingId: "HP2026_0301", outcome: "approved_conditions" },
      { filingId: "HP2026_0302", outcome: "denied_or_cor" },
    ],
    chicagoTimestamps: [
      "2026-10-29T02:30:00Z", // 21:30 CDT previous day
      "2026-10-29T12:15:00+00:00",
      "2026-01-15T05:30:00Z", // 23:30 CST previous day
      "2026-10-28",
    ],
  };

  const jsComputed = {
    sanitize: sharedVectors.texts.map((t) => sanitizeActivityText(t)),
    collapse: sharedVectors.texts.map((t) => collapseUpper(t)),
    fnv: sharedVectors.texts.map((t) => fnv1a32Hex(collapseUpper(t))),
    hcads: sharedVectors.hcads.map((h) => normalizeHcad13(h)),
    autoIds: sharedVectors.autoChecks.map((x) => buildAutoCheckEventId(x.propId, x.rowId, x.chk)),
    manualIds: sharedVectors.manualChecks.map((x) =>
      buildManualCheckEventId(x.propId, x.rowId, x.chk)
    ),
    statusIds: sharedVectors.watchStatuses.map((x) =>
      buildWatchStatusEventId(x.propId, x.rowId, x.status, x.dateIso)
    ),
    degradedId: buildSweepDegradedEventId("2026-10-29", ["CHK-14", "chk-05", "chk-14"]),
    degradedEmptyId: buildSweepDegradedEventId("2026-10-29", []),
    staffPropIds: sharedVectors.staffProps.map((x) =>
      buildStaffPropEventId(x.propId, x.action, x.dateIso)
    ),
    staffReviewIds: sharedVectors.staffReviews.map((x) =>
      buildStaffReviewEventId(x.cycleKey, x.appNo, x.tier)
    ),
    agendaCycleIds: sharedVectors.agendaCycles.map((c) => buildAgendaCycleEventId(c)),
    agendaItemIds: sharedVectors.agendaItems.map((x) =>
      buildAgendaItemEventId(x.cycleKey, x.appNo, x.kind)
    ),
    permitCohIds: sharedVectors.permitsCoh.map((x) =>
      buildPermitCohEventId(x.hcadRaw, x.projectNo, x.fallbackDesc, x.dateIso)
    ),
    permitDshsIds: sharedVectors.permitsDshs.map((x) =>
      buildPermitDshsEventId(x.hcadRaw, x.notifNo, x.fallbackAddr, x.dateIso)
    ),
    hahcCycleIds: sharedVectors.hahcCycles.map((d) => buildHahcCycleEventId(d)),
    hahcGuidelineIds: sharedVectors.hahcGuidelines.map((x) =>
      buildHahcGuidelineEventId(x.meetingDate, x.titleOrUrl)
    ),
    hahcFilingIds: sharedVectors.hahcFilings.map((x) =>
      buildHahcFilingEventId(x.filingId, x.outcome)
    ),
    chicagoDates: sharedVectors.chicagoTimestamps.map((ts) => toChicagoDateIso(ts)),
  };

  const pyScript = `
import json, sys
sys.path.insert(0, "pipeline")
from atlas_pipeline import build_monitor_activity_log as bmal

v = json.loads(sys.stdin.read())
out = {
    "sanitize": [bmal.sanitize_text(t) for t in v["texts"]],
    "collapse": [bmal.collapse_upper(t) for t in v["texts"]],
    "fnv": [bmal.fnv1a32_hex(bmal.collapse_upper(t)) for t in v["texts"]],
    "hcads": [bmal.normalize_hcad_13(h) for h in v["hcads"]],
    "autoIds": [bmal.build_auto_check_event_id(x["propId"], x["rowId"], x["chk"]) for x in v["autoChecks"]],
    "manualIds": [bmal.build_manual_check_event_id(x["propId"], x["rowId"], x["chk"]) for x in v["manualChecks"]],
    "statusIds": [bmal.build_watch_status_event_id(x["propId"], x["rowId"], x["status"], x["dateIso"]) for x in v["watchStatuses"]],
    "degradedId": bmal.build_sweep_degraded_event_id("2026-10-29", ["CHK-14", "chk-05", "chk-14"]),
    "degradedEmptyId": bmal.build_sweep_degraded_event_id("2026-10-29", []),
    "staffPropIds": [bmal.build_staff_prop_event_id(x["propId"], x["action"], x["dateIso"]) for x in v["staffProps"]],
    "staffReviewIds": [bmal.build_staff_review_event_id(x["cycleKey"], x["appNo"], x["tier"]) for x in v["staffReviews"]],
    "agendaCycleIds": [bmal.build_agenda_cycle_event_id(c) for c in v["agendaCycles"]],
    "agendaItemIds": [bmal.build_agenda_item_event_id(x["cycleKey"], x["appNo"], x["kind"]) for x in v["agendaItems"]],
    "permitCohIds": [bmal.build_permit_coh_event_id(x["hcadRaw"], x["projectNo"], x["fallbackDesc"], x["dateIso"]) for x in v["permitsCoh"]],
    "permitDshsIds": [bmal.build_permit_dshs_event_id(x["hcadRaw"], x["notifNo"], x["fallbackAddr"], x["dateIso"]) for x in v["permitsDshs"]],
    "hahcCycleIds": [bmal.build_hahc_cycle_event_id(d) for d in v["hahcCycles"]],
    "hahcGuidelineIds": [bmal.build_hahc_guideline_event_id(x["meetingDate"], x["titleOrUrl"]) for x in v["hahcGuidelines"]],
    "hahcFilingIds": [bmal.build_hahc_filing_event_id(x["filingId"], x["outcome"]) for x in v["hahcFilings"]],
    "chicagoDates": [bmal.to_chicago_date_iso(ts) for ts in v["chicagoTimestamps"]],
}
print(json.dumps(out))
`;

  const pyRaw = execFileSync("python3", ["-S", "-c", pyScript], {
    cwd: REPO_ROOT,
    input: JSON.stringify(sharedVectors),
    encoding: "utf8",
  });
  const pyComputed = JSON.parse(pyRaw.trim());
  assert.deepEqual(jsComputed, pyComputed, "UT-4: Python and Node Event IDs must match byte-for-byte");

  // Also verify end-to-end event ID AND `kind` enum parity between Python diff emitter and JS live synthesizer
  const liveParitySynth = synthesizeLiveMonitorEvents({
    staticLog: {
      version: 1,
      generatedAt: "2026-10-28T12:00:00Z",
      events: [
        {
          id: "hahc:filing:HP2026_0301:pending-agenda",
          stream: "permits",
          kind: "hahc_filing_added",
          severity: "critical",
          adverse: true,
          date: "2026-10-08",
          localDate: "2026-10-08",
          timestamp: "2026-10-08T12:00:00Z",
          discoveredAt: "2026-10-08T12:00:00Z",
          title: "Seeded HAHC pending filing",
          summary: "Baseline",
        },
      ],
      dailyRuns: [],
    },
    watchedProperties: [
      {
        id: "prop-kirby-mansion",
        propertyName: "John Henry Kirby Mansion",
        address: "2000 Smith St.",
        hcadAccount: "152300000001", // unpadded 12-digit HCAD
        checklist: [
          {
            id: "chk-14",
            activity: "Demolition & Building Permits",
            status: "Flagged",
            humanStatusEdited: true,
            updatedAt: "2026-10-29T14:00:00Z",
            checks: [],
          },
        ],
      },
    ],
    permitAlertsPayload: {
      generated_at: "2026-10-29T14:05:00Z",
      alerts: [
        {
          hcad_num: "152300000001",
          title: "John Henry Kirby Mansion",
          address: "2000 Smith St.",
          coh_permits: [{ project_no: "26090093", date: "2026-10-29", is_structural_demo: true }],
          dshs_notifications: [{ notification_number: "2026005825", filing_date: "2026-10-29" }],
        },
      ],
    },
    hahcPayload: {
      generated_at: "2026-10-29T14:10:00Z",
      current_cycle: { meeting_date: "2026-11-05", guideline_updates: [] },
      filings: [
        {
          filing_id: "HP2026_0301",
          meeting_date: "2026-10-08",
          address: "1115 Heights Blvd",
          app_type: "Demolition",
          app_category: "demolition_relocation",
          outcome: "approved",
          updated_at: "2026-10-29T14:10:00Z",
        },
      ],
    },
    nowIso: "2026-10-29T15:00:00Z",
  });

  const synthById = new Map(liveParitySynth.events.map((e) => [e.id, e]));
  assert.equal(synthById.has("permit:coh:0152300000001:26090093"), true);
  assert.equal(synthById.has("permit:dshs:0152300000001:2026005825"), true);
  assert.equal(synthById.get("hahc:filing:HP2026_0301:approved")?.kind, "hahc_filing_outcome");
  assert.equal(
    synthById.get("wl:status:prop-kirby-mansion:chk-14:flagged:2026-10-29")?.kind,
    "watchlist_status_escalation",
    "UT-4: JS status escalation kind must match Python watchlist_status_escalation"
  );

  // Follow-up manual notes on an already-Flagged historical row (statusChangedAt preserved in the past,
  // updatedAt bumped to now) MUST emit only watchlist_manual_check without fabricating a false escalation.
  const followUpOnFlagged = synthesizeLiveMonitorEvents({
    staticLog: {
      baselineEstablishedAt: "2026-10-29T12:00:00Z",
      events: [],
      dailyRuns: [],
    },
    watchedProperties: [
      {
        id: "prop-kirby-mansion",
        propertyName: "Former Kirby Mansion Site",
        address: "2000 Smith St",
        hcadAccount: "0152300000001",
        keyDates: { monitoringStarted: "2026-01-15" },
        updatedAt: "2026-10-29T15:00:00Z",
        checklist: [
          {
            id: "chk-02",
            activity: "County Clerk Deeds",
            status: "Flagged",
            humanStatusEdited: true,
            statusChangedAt: "2026-03-12T00:00:00Z",
            updatedAt: "2026-10-29T15:00:00Z",
            lastChecked: "2026-10-29",
            checks: [
              {
                date: "2026-10-29",
                source: "manual",
                loggedAt: "2026-10-29T15:00:00Z",
                result: "Staff follow-up note on already-Flagged deed row",
              },
            ],
          },
        ],
      },
    ],
    nowIso: "2026-10-29T15:05:00Z",
  });
  assert.equal(
    followUpOnFlagged.events.some((e) => e.kind === "watchlist_manual_check"),
    true,
    "UT-4: Logging a manual check on an already-Flagged row must emit watchlist_manual_check"
  );
  assert.equal(
    followUpOnFlagged.events.some((e) => e.kind === "watchlist_status_escalation"),
    false,
    "UT-4: Logging a manual check on an already-Flagged row must NOT fabricate a watchlist_status_escalation"
  );
}

// ---------------------------------------------------------------------------
// UT-5: Boot Routing Precedence, Watermark Math, 500-ID Cap, Filtering & Export
// ---------------------------------------------------------------------------
{
  // 1. Exhaustive 3-mode boot precedence matrix
  const precedenceCases = [
    { q: "?mode=activity", stored: "agenda", expected: "activity", override: true },
    { q: "?mode=watchlist&cycle=2026-10-29", stored: "activity", expected: "watchlist", override: true },
    { q: "?tab=briefing", stored: "watchlist", expected: "activity", override: true },
    { q: "?tab=plats", stored: "activity", expected: "agenda", override: true },
    { q: "?watch=prop-kirby-mansion", stored: "activity", expected: "watchlist", override: true },
    { q: "?hcad=0152300000001", stored: "activity", expected: "watchlist", override: true },
    { q: "?address=2000+Smith+St", stored: "activity", expected: "watchlist", override: true },
    { q: "?cycle=2026-10-29", stored: "activity", expected: "agenda", override: true },
    { q: "?app=2026-1585", stored: "activity", expected: "agenda", override: true },
    { q: "?id=2026-10-29::2026-1585", stored: "watchlist", expected: "agenda", override: true },
    { q: "", stored: "activity", expected: "activity", override: false },
    { q: "", stored: "watchlist", expected: "watchlist", override: false },
    { q: "", stored: "invalid-junk", expected: "agenda", override: false },
    { q: "", stored: "", expected: "agenda", override: false },
  ];
  for (const tc of precedenceCases) {
    const res = resolveInitialMonitorMode({ searchString: tc.q, storedLastMode: tc.stored });
    assert.equal(res.mode, tc.expected, `Precedence failed for q=${tc.q} stored=${tc.stored}`);
    assert.equal(res.explicitUrlOverride, tc.override);
  }

  // 2. Implicit first-visit 7-day watermark vs explicit ISO watermark + readEventIds
  const sampleEvents = [
    {
      id: "ev-old-15d",
      stream: "agenda",
      severity: "info",
      adverse: false,
      localDate: "2026-10-14",
      timestamp: "2026-10-14T12:00:00Z",
      title: "Older plat cycle",
      summary: "Outside 7d window",
    },
    {
      id: "ev-5d-warn",
      stream: "staff",
      severity: "warning",
      adverse: false,
      localDate: "2026-10-25",
      timestamp: "2026-10-25T15:00:00Z",
      title: "Montrose review note",
      summary: "Staff confirmed Tier 1",
      cycleKey: "2026-10-29",
      appNo: "2026-1585",
      occKey: "2026-10-29::2026-1585",
    },
    {
      id: "ev-today-crit",
      stream: "permits",
      severity: "critical",
      adverse: true,
      localDate: "2026-10-29",
      timestamp: "2026-10-29T16:30:00Z", // Ahead of injected nowIso below to test clock-skew safety!
      title: "Kirby Mansion Demolition Permit\u2014Urgent",
      summary: "2000 Smith St\u2014BLD DEMO",
      address: "2000 Smith St.",
      hcad: "0152300000001",
      watchPropertyId: "prop-kirby-mansion",
      checkId: "chk-14",
    },
    {
      id: "ev-today-info",
      stream: "watchlist",
      severity: "info",
      adverse: false,
      localDate: "2026-10-29",
      timestamp: "2026-10-29T14:00:00Z",
      title: "Kirby HCAD check",
      summary: "Routine match",
      watchPropertyId: "prop-kirby-mansion",
      checkId: "chk-05",
    },
  ];

  const wmFirstVisit = computeWatermarkState({
    events: sampleEvents,
    caughtUpAt: "",
    readEventIds: [],
    nowIso: "2026-10-29T15:00:00Z",
  });
  assert.equal(wmFirstVisit.isImplicitFirstVisit, true);
  assert.equal(wmFirstVisit.implicitFloorDate, "2026-10-23");
  assert.equal(wmFirstVisit.unreadCount, 3);
  assert.equal(wmFirstVisit.unreadAdverseCount, 1);
  assert.equal(wmFirstVisit.unreadIds.has("ev-old-15d"), false);

  const wmExplicit = computeWatermarkState({
    events: sampleEvents,
    caughtUpAt: "2026-10-28T20:00:00Z",
    readEventIds: ["ev-today-info"],
    nowIso: "2026-10-29T15:00:00Z",
  });
  assert.equal(wmExplicit.isImplicitFirstVisit, false);
  assert.equal(wmExplicit.unreadCount, 1);
  assert.equal(wmExplicit.unreadAdverseCount, 1);
  assert.equal(wmExplicit.unreadIds.has("ev-today-crit"), true);

  // 3. buildMarkCaughtUpState clock-skew protection (>= max event timestamp) + strict 500-ID FIFO cap
  const caughtState = buildMarkCaughtUpState({
    events: sampleEvents,
    prevReadEventIds: ["legacy-1", "legacy-2"],
    nowIso: "2026-10-29T15:00:00Z", // Earlier than ev-today-crit (16:30Z)!
  });
  assert.equal(
    caughtState.caughtUpAt,
    "2026-10-29T16:30:00.000Z",
    "caughtUpAt must advance to max(nowIso, maxEventTimestamp) to defeat clock skew"
  );
  const afterCatchUp = computeWatermarkState({
    events: sampleEvents,
    caughtUpAt: caughtState.caughtUpAt,
    readEventIds: caughtState.readEventIds,
    nowIso: "2026-10-29T15:00:05Z",
  });
  assert.equal(afterCatchUp.unreadCount, 0);

  // Boundary precision: even if FIFO eviction drops an ID from readEventIds, an event stamped
  // exactly at caughtUpAt (evMs === wmMs) must remain read (strictly evMs > wmMs is unread).
  const afterFifoEvictionAtExactWatermark = computeWatermarkState({
    events: sampleEvents,
    caughtUpAt: caughtState.caughtUpAt,
    readEventIds: [],
    nowIso: "2026-10-29T17:00:00Z",
  });
  assert.equal(
    afterFifoEvictionAtExactWatermark.unreadCount,
    0,
    "Events stamped at exact caughtUpAt boundary must remain read even when evicted from readEventIds"
  );

  const manyEvents = Array.from({ length: 620 }, (_, i) => ({
    id: `bulk-ev-${String(i).padStart(4, "0")}`,
    localDate: "2026-10-29",
    timestamp: `2026-10-29T12:00:${String(i % 60).padStart(2, "0")}Z`,
  }));
  const capped = buildMarkCaughtUpState({
    events: manyEvents,
    prevReadEventIds: ["very-old-id-1", "very-old-id-2"],
    nowIso: "2026-10-29T18:00:00Z",
  });
  assert.equal(MAX_STORED_READ_EVENT_IDS, 500);
  assert.equal(capped.readEventIds.length, 500);
  assert.equal(capped.readEventIds.includes("very-old-id-1"), false);

  // 4. filterActivityEvents, groupActivityEventsByDate, buildEntityUnreadIndex, formatActivityBriefingText
  const sinceVisit = filterActivityEvents({
    events: sampleEvents,
    watermarkState: wmFirstVisit,
    rangeMode: "since_last_visit",
    nowIso: "2026-10-29T15:00:00Z",
  });
  assert.deepEqual(
    sinceVisit.map((e) => e.id),
    ["ev-today-crit", "ev-5d-warn", "ev-today-info"],
    "Briefing sort must order critical -> warning -> info"
  );

  const byDateJump = filterActivityEvents({
    events: sampleEvents,
    watermarkState: wmExplicit,
    rangeMode: "since_last_visit",
    selectedDate: "2026-10-14",
    nowIso: "2026-10-29T15:00:00Z",
  });
  assert.deepEqual(byDateJump.map((e) => e.id), ["ev-old-15d"]);

  const byStreamAndQuery = filterActivityEvents({
    events: sampleEvents,
    watermarkState: wmFirstVisit,
    rangeMode: "all",
    streamFilter: "permits",
    query: "0152300000001",
    nowIso: "2026-10-29T15:00:00Z",
  });
  assert.deepEqual(byStreamAndQuery.map((e) => e.id), ["ev-today-crit"]);

  const grouped = groupActivityEventsByDate(sinceVisit);
  assert.deepEqual(grouped.map((g) => g.date), ["2026-10-29", "2026-10-25"]);

  const unreadIdx = buildEntityUnreadIndex(sampleEvents, wmFirstVisit);
  assert.equal(unreadIdx.watchPropUnread.get("prop-kirby-mansion")?.count, 2);
  assert.equal(unreadIdx.watchPropUnread.get("prop-kirby-mansion")?.hasCritical, true);
  assert.equal(unreadIdx.watchCheckUnread.has("prop-kirby-mansion::chk-14"), true);
  assert.equal(unreadIdx.agendaOccUnread.get("2026-10-29::2026-1585")?.count, 1);
  assert.equal(unreadIdx.agendaAppUnread.get("2026-10-29::2026-1585")?.count, 1);

  const briefingTxt = formatActivityBriefingText({
    events: sinceVisit,
    dailyRun: {
      date: "2026-10-29",
      succeededAgencies: 7,
      totalAgencies: 8,
      failedAgencies: ["chk-10"],
      propertiesSwept: 4,
    },
    rangeLabel: "Since Last Visit\u2014Morning",
    nowIso: "2026-10-29T15:00:00Z",
  });
  assert.equal(briefingTxt.includes("\u2014"), false, "Briefing export must contain zero U+2014");
  assert.equal(briefingTxt.includes("DEGRADED - 7/8 agencies OK (Failed: chk-10)"), true);
  assert.equal(briefingTxt.indexOf("[CRITICAL]") < briefingTxt.indexOf("[REVIEW]"), true);
}

// ---------------------------------------------------------------------------
// UT-6: Temporal Boundaries, Backdated Manual Check Visibility & Ledger Compaction
// ---------------------------------------------------------------------------
{
  // (a) 350 historical >90-day HAHC filings + older pre-baseline Watchlist checks MUST NEVER resurrect
  const historicalFilings = Array.from({ length: 350 }, (_, i) => ({
    filing_id: `HP2024_${String(i).padStart(4, "0")}`,
    meeting_date: i < 300 ? "2024-05-16" : "2026-09-10", // both >90d and pre-baseline <=90d rows
    address: `${100 + i} Heights Blvd`,
    app_type: "Alteration / Addition",
    app_category: "alteration_addition",
    outcome: "approved",
  }));

  const staticBaselineLog = {
    version: 1,
    generatedAt: "2026-10-29T12:15:00.123456+00:00", // Python microsecond +00:00 format
    events: [],
    dailyRuns: [
      {
        date: "2026-10-29",
        lastRunAt: "2026-10-29T12:15:00Z",
        runCount: 1,
        sweepsExecuted: true,
        propertiesSwept: 2,
        totalAgencies: 8,
        succeededAgencies: 8,
        failedAgencies: [],
        allOk: true,
        eventCount: 0,
        adverseEventCount: 0,
        byStream: { watchlist: 0, agenda: 0, staff: 0, permits: 0 },
      },
    ],
  };

  const nonResurrectSynth = synthesizeLiveMonitorEvents({
    staticLog: staticBaselineLog,
    watchedProperties: [
      {
        id: "prop-kirby-mansion",
        propertyName: "John Henry Kirby Mansion",
        address: "2000 Smith St.",
        keyDates: { monitoringStarted: "2026-09-22" },
        updatedAt: "2026-10-28T10:00:00Z", // Pre-baseline
        checklist: [
          {
            id: "chk-03",
            activity: "Deed Transfers",
            status: "Complete",
            lastChecked: "2026-10-05",
            checks: [{ date: "2026-10-05", result: "Recorded deed RP-2026-399473" }],
          },
        ],
      },
      {
        id: "prop-montrose",
        propertyName: "Montrose Courtyard",
        address: "4310 Montrose Blvd",
        keyDates: { monitoringStarted: "2026-10-15" },
        updatedAt: "2026-10-29T14:30:00.000Z", // Strictly post-baseline cloud edit!
        updatedBy: "Emily",
        checklist: [],
      },
    ],
    hahcPayload: {
      generated_at: "2026-10-29T14:00:00Z", // Even when HAHC JSON regenerated post-baseline!
      current_cycle: { meeting_date: "2026-10-08", guideline_updates: [] },
      filings: historicalFilings,
    },
    nowIso: "2026-10-29T15:00:00Z",
  });

  assert.equal(
    nonResurrectSynth.events.length,
    1,
    `Expected exactly 1 post-baseline event, got ${nonResurrectSynth.events.length}: ${nonResurrectSynth.events.map((e) => e.id).join(", ")}`
  );
  assert.equal(nonResurrectSynth.events[0].id, "staff:prop:prop-montrose:edited:2026-10-29");

  // Also verify HTTP-404 fail-open fallback (staticLog: null) bounds strictly to rolling 7 days
  const failOpenSynth = synthesizeLiveMonitorEvents({
    staticLog: null,
    watchedProperties: [
      {
        id: "prop-recent",
        propertyName: "Recent Add",
        address: "500 Main St",
        keyDates: { monitoringStarted: "2026-10-27" },
        checklist: [],
      },
      {
        id: "prop-older",
        propertyName: "Older Sept Add",
        address: "600 Main St",
        keyDates: { monitoringStarted: "2026-09-22" },
        checklist: [],
      },
    ],
    hahcPayload: {
      generated_at: "2026-10-29T12:00:00Z",
      filings: historicalFilings,
    },
    nowIso: "2026-10-29T15:00:00Z",
  });
  assert.deepEqual(failOpenSynth.events.map((e) => e.id), ["staff:prop:prop-recent:added"]);

  // (b) Backdated manual check stamped with loggedAt surfaces unread WITHOUT marking older sibling checks unread,
  //     and loggedAt survives 3-way reconcileWatchlistProperty intact
  const staticKirby = normalizeWatchProperty({
    id: "prop-kirby-mansion",
    propertyName: "John Henry Kirby Mansion",
    address: "2000 Smith St.",
    keyDates: { monitoringStarted: "2026-09-22" },
    updatedAt: "2026-10-25T12:00:00Z",
    checklist: [
      {
        id: "chk-08",
        activity: "Pending Plans (Open Records & ILMS)",
        status: "Flagged",
        lastChecked: "2026-10-22",
        checks: [
          { date: "2026-10-05", result: "Initial GovQA request filed" },
          { date: "2026-10-22", result: "ILMS #26052089 returned for revisions" },
        ],
      },
    ],
  });
  const localEditedKirby = JSON.parse(JSON.stringify(staticKirby));
  localEditedKirby.updatedAt = "2026-10-29T15:00:00Z";
  const chk08Local = findChecklistRow(localEditedKirby.checklist, "chk-08");
  chk08Local.updatedAt = "2026-10-29T15:00:00Z";
  chk08Local.checks.push({
    date: "2026-10-15", // Backdated research date BEFORE caughtUpAt!
    loggedAt: "2026-10-29T15:00:00Z", // Wall-clock stamp AFTER caughtUpAt!
    result: "Received belated GovQA PDF packet from Oct 15 intake",
    source: "manual",
  });

  const reconciledKirby = reconcileWatchlistProperty(staticKirby, null, localEditedKirby);
  const reconciledChk08 = findChecklistRow(reconciledKirby.checklist, "chk-08");
  const backdatedEntry = reconciledChk08.checks.find((c) => c.date === "2026-10-15");
  assert.equal(backdatedEntry?.loggedAt, "2026-10-29T15:00:00Z");

  const backdatedSynth = synthesizeLiveMonitorEvents({
    staticLog: {
      version: 1,
      generatedAt: "2026-10-29T12:00:00Z",
      events: [],
      dailyRuns: [],
    },
    watchedProperties: [reconciledKirby],
    nowIso: "2026-10-29T15:05:00Z",
  });
  const manualEvs = backdatedSynth.events.filter((e) => e.kind === "watchlist_manual_check");
  assert.equal(
    manualEvs.length,
    1,
    "Only the newly stamped backdated manual check may synthesize; older sibling checks must stay quiet"
  );
  assert.equal(manualEvs[0].date, "2026-10-15");
  assert.equal(manualEvs[0].loggedAt, "2026-10-29T15:00:00Z");

  const wmBackdated = computeWatermarkState({
    events: backdatedSynth.events,
    caughtUpAt: "2026-10-28T20:00:00Z",
    readEventIds: [],
    nowIso: "2026-10-29T15:05:00Z",
  });
  assert.equal(wmBackdated.unreadIds.has(manualEvs[0].id), true);

  // (c) Quiet-day compaction collapses contiguous healthy 0-event days while NEVER hiding failedAgencies runs,
  //     AND live degraded sweep health preserves eventCount === 0 on a quiet day + evicts stale same-day notice
  const quietDegradedSynth = synthesizeLiveMonitorEvents({
    staticLog: {
      version: 1,
      generatedAt: "2026-10-29T12:00:00Z",
      events: [
        {
          id: "wl:sweep:2026-10-29:degraded:chk-05",
          stream: "watchlist",
          kind: "watchlist_sweep_degraded",
          severity: "warning",
          adverse: false,
          date: "2026-10-29",
          localDate: "2026-10-29",
          timestamp: "2026-10-29T12:00:00Z",
          discoveredAt: "2026-10-29T12:00:00Z",
          title: "Stale morning degraded notice",
          summary: "Should be superseded by newer watchlistSweepHealth on same day",
        },
      ],
      dailyRuns: [
        { date: "2026-10-29", eventCount: 0, allOk: false, failedAgencies: ["chk-05"], succeededAgencies: 7, totalAgencies: 8 },
        { date: "2026-10-28", eventCount: 0, allOk: true, failedAgencies: [], succeededAgencies: 8, totalAgencies: 8 },
        { date: "2026-10-27", eventCount: 0, allOk: true, failedAgencies: [], succeededAgencies: 8, totalAgencies: 8 },
        { date: "2026-10-26", eventCount: 0, allOk: true, failedAgencies: [], succeededAgencies: 8, totalAgencies: 8 },
        { date: "2026-10-25", eventCount: 0, allOk: false, failedAgencies: ["chk-10"], succeededAgencies: 7, totalAgencies: 8 },
        { date: "2026-10-24", eventCount: 0, allOk: true, failedAgencies: [], succeededAgencies: 8, totalAgencies: 8 },
        { date: "2026-10-23", eventCount: 0, allOk: true, failedAgencies: [], succeededAgencies: 8, totalAgencies: 8 },
      ],
    },
    watchlistSweepHealth: {
      timestamp: "2026-10-29T18:00:00Z",
      sweepsExecuted: true,
      propertiesSwept: 2,
      totalAgencies: 8,
      succeededAgencies: 7,
      failedAgencies: ["chk-10"],
      allOk: false,
    },
    nowIso: "2026-10-29T18:05:00Z",
  });

  const todayDegradedRun = quietDegradedSynth.dailyRuns.find((r) => r.date === "2026-10-29");
  assert.equal(
    todayDegradedRun.eventCount,
    0,
    "UT-6(c): Operational degraded sweep notice must not inflate substantive dailyRuns[].eventCount above 0"
  );
  assert.deepEqual(todayDegradedRun.failedAgencies, ["chk-10"]);
  assert.equal(
    quietDegradedSynth.events.some((e) => e.id === "wl:sweep:2026-10-29:degraded:chk-05"),
    false,
    "UT-6(c): Stale same-day degraded sweep notice must be superseded by latest watchlistSweepHealth"
  );
  assert.equal(
    quietDegradedSynth.events.some((e) => e.id === "wl:sweep:2026-10-29:degraded:chk-10"),
    true
  );

  const compacted = compactDailyRunsForLedger(quietDegradedSynth.dailyRuns);
  assert.deepEqual(
    compacted.map((x) => (x.type === "quiet_range" ? `range:${x.rangeKey}:${x.count}` : `day:${x.run.date}`)),
    [
      "day:2026-10-29", // degraded (chk-10) -> NEVER hidden!
      "range:2026-10-28..2026-10-26:3", // 3 healthy quiet days collapsed
      "day:2026-10-25", // 0 events BUT degraded (chk-10) -> NEVER hidden!
      "range:2026-10-24..2026-10-23:2", // 2 healthy quiet days collapsed
    ]
  );

  const expandedCompacted = compactDailyRunsForLedger(quietDegradedSynth.dailyRuns, {
    expandedRangeKeys: ["2026-10-24..2026-10-23"],
  });
  assert.equal(expandedCompacted.some((x) => x.type === "day" && x.run?.date === "2026-10-24"), true);
  assert.equal(expandedCompacted.some((x) => x.type === "day" && x.run?.date === "2026-10-23"), true);
}

// ---------------------------------------------------------------------------
// UT-7: XSS / URL Sanitization, Alert-to-Watchlist Correlation & 1-Click Promotion
// ---------------------------------------------------------------------------
{
  // 1. HTML escaping & unsafe URL scheme stripping
  const hostileEvent = {
    id: 'ev"><img src=x onerror=alert(1)>',
    stream: "permits",
    kind: "permit_coh_alert",
    severity: "critical",
    adverse: true,
    localDate: "2026-10-29",
    title: '<script>alert("xss")</script> & Mansion\u2014Alert',
    summary: "Owner 'O\"Brien' <iframe src=javascript:alert(1)>",
    address: "2000 <Smith> St.",
    hcad: "0152300000001",
    designation: 'Landmark "A" & <B>',
    lat: 29.751432,
    lng: -95.374833,
    links: [
      { label: "Evil JS", url: "javascript:alert(document.cookie)" },
      { label: "Mixed JS", url: "  JaVaScRiPt:alert(1)" },
      { label: "Data URI", url: "data:text/html,<script>alert(1)</script>" },
      { label: "VBScript", url: "vbscript:msgbox(1)" },
      { label: "Local File", url: "file:///etc/passwd" },
      { label: "Control Char", url: "https://example.com/\u0000bad" },
      { label: '<b>Valid Record</b>', url: "https://cohtora.houstontx.gov/permit?a=1&b=2" },
      { label: "Duplicate Valid", url: "https://cohtora.houstontx.gov/permit?a=1&b=2" },
    ],
  };

  const safe = sanitizeActivityEventForRender(hostileEvent);
  for (const field of [
    safe.safeId,
    safe.safeTitle,
    safe.safeSummary,
    safe.safeAddress,
    safe.safeDesignation,
  ]) {
    assert.equal(field.includes("<script>"), false);
    assert.equal(field.includes("<iframe"), false);
    assert.equal(field.includes("<img"), false);
    assert.equal(field.includes("\u2014"), false);
  }
  assert.equal(safe.safeLinks.length, 1, "Only unique http/https links may survive sanitization");
  assert.equal(safe.safeLinks[0].url, "https://cohtora.houstontx.gov/permit?a=1&b=2");
  assert.equal(safe.safeLinks[0].safeLabel, "&lt;b&gt;Valid Record&lt;/b&gt;");
  assert.equal(safe.safeLinks[0].safeUrl.includes("&amp;b=2"), true);
  assert.equal(safe.atlasMapHref.startsWith("index.html#"), true);
  assert.equal(safe.atlasMapHref.includes("hcad=0152300000001"), true);

  assert.equal(buildEventAtlasMapHref({ stream: "staff", title: "No coords or hcad" }), "");

  // 2. findMatchingWatchPropertyForAlert across explicit ID, padded/unpadded HCADs, and perimeter addresses
  const watchedPool = [
    normalizeWatchProperty({
      id: "prop-kirby-mansion",
      propertyName: "John Henry Kirby Mansion",
      address: "2000 Smith St.",
      secondaryAddresses: ["530 Pierce St."],
      hcadAccount: "0152300000001",
      searchTerms: { altAddresses: ["2000 Brazos St."] },
      trackerStatus: "Active / High Priority",
    }),
    normalizeWatchProperty({
      id: "prop-archived",
      propertyName: "Archived House",
      address: "999 Archived Ln",
      hcadAccount: "0990099009900",
      trackerStatus: "Archived (Tier 3)",
    }),
  ];

  assert.equal(
    findMatchingWatchPropertyForAlert({ watchPropertyId: "prop-kirby-mansion" }, watchedPool)?.id,
    "prop-kirby-mansion"
  );
  assert.equal(
    findMatchingWatchPropertyForAlert({ hcad: "152300000001" }, watchedPool)?.id,
    "prop-kirby-mansion",
    "Unpadded 12-digit HCAD must match 13-digit Watchlist parcel"
  );
  assert.equal(
    findMatchingWatchPropertyForAlert(
      { permitMeta: { hcad_num: "0410010010001", related_hcads: ["0152300000001"] } },
      watchedPool
    )?.id,
    "prop-kirby-mansion"
  );
  assert.equal(
    findMatchingWatchPropertyForAlert({ address: "530 PIERCE STREET, HOUSTON TX" }, watchedPool)?.id,
    "prop-kirby-mansion"
  );
  assert.equal(
    findMatchingWatchPropertyForAlert({ address: "2000 Brazos Street" }, watchedPool)?.id,
    "prop-kirby-mansion"
  );
  assert.equal(
    findMatchingWatchPropertyForAlert({ hcad: "0990099009900" }, watchedPool),
    null,
    "Archived Tier 3 properties must not suppress Watchlist promotion"
  );

  // 3. buildWatchPropertyFromPermitAlert for COH demolition & DSHS asbestos alerts
  const promotedCoh = buildWatchPropertyFromPermitAlert(
    {
      summary: "2009 W Gray St: DEMO COMMERCIAL STRUCTURE issued 2026-10-29.",
      links: [{ label: "Houston Sold Permits", url: "https://cohtora.houstontx.gov/approot/soldpermits/online_permit.htm" }],
      permitMeta: {
        alertKind: "coh",
        title: "River Oaks Theater",
        address: "2009 W Gray St",
        hcad_num: "410170020150", // 12-digit unpadded
        related_hcads: ["0410170020151"],
        building_id: "bld_ro_1939",
        year_built: 1939,
        landmark_name: "River Oaks Theater",
        historic_district: "River Oaks Shopping Area",
        good_brick_summary: "2024 Good Brick Award",
        owner: "RIVER OAKS PROP LLC",
        lat: 29.7531,
        lng: -95.4082,
        record: {
          project_no: "26099555",
          date: "2026-10-29",
          permit_type_label: "Building Demolition",
          use_desc: "BLD DEMO",
          buyer: "ACME DEMO LLC",
        },
      },
    },
    { reviewer: "Emily", nowIso: "2026-10-29T15:30:00Z" }
  );

  assert.equal(promotedCoh.id, "prop-watch-0410170020150");
  assert.equal(promotedCoh.tier, "1");
  assert.equal(promotedCoh.tierConfirmed, true);
  assert.equal(promotedCoh.hcadAccount, "0410170020150, 0410170020151");
  assert.equal(
    promotedCoh.designation,
    "City Landmark: River Oaks Theater | Historic District: River Oaks Shopping Area | 2024 Good Brick Award"
  );
  assert.equal(promotedCoh.ilmsProjectNo, "26099555");
  assert.deepEqual(promotedCoh.searchTerms.projectNumbers, ["26099555"]);
  const cohChk14 = findChecklistRow(promotedCoh.checklist, "chk-14");
  assert.equal(cohChk14.status, "Flagged");
  assert.equal(cohChk14.checks[0].loggedAt, "2026-10-29T15:30:00Z");

  const promotedDshs = buildWatchPropertyFromPermitAlert(
    {
      summary: "DSHS Asbestos Notification #2026009911",
      permitMeta: {
        alertKind: "dshs",
        title: "Historic Masonic Lodge",
        address: "1500 Fannin St",
        hcad_num: "0010500000012",
        year_built: 1926,
        record: {
          notification_number: "2026009911",
          work_type: "Abatement",
          filing_date: "2026-10-28",
          start_date: "2026-11-10",
          end_date: "2026-11-22",
          facility_owner: "LODGE HOLDINGS LLC",
          print_action_url: "https://vo.ras.dshs.state.tx.us/datamart/notificationPrintAction.do?licId=999",
        },
      },
    },
    { reviewer: "Emily", nowIso: "2026-10-29T15:35:00Z" }
  );
  assert.equal(promotedDshs.keyDates.asbestosAbatementWindow, "2026-11-10 to 2026-11-22");
  const dshsChk15 = findChecklistRow(promotedDshs.checklist, "chk-15");
  assert.equal(dshsChk15.status, "Flagged");
  assert.equal(dshsChk15.checks[0].loggedAt, "2026-10-29T15:35:00Z");
  assert.equal(
    dshsChk15.additionalResourceUrl,
    "https://vo.ras.dshs.state.tx.us/datamart/notificationPrintAction.do?licId=999"
  );

  // 4. Multi-record raw active_permit_alerts.json promotion seeds BOTH chk-14 and chk-15 cleanly
  const promotedRawAlert = buildWatchPropertyFromPermitAlert(
    {
      hcad_num: "0152300000001",
      related_hcads: ["0152300000002"],
      building_name: "Historic Corner Building",
      address: "2000 Smith St.",
      secondary_addresses: ["530 Pierce St."],
      year_built: 1928,
      lon: -95.3741,
      lat: 29.7501,
      severity: "critical",
      preservation_note: "Historic Tudor landmark.",
      coh_permits: [
        {
          project_no: "26090093",
          date: "2026-10-14",
          permit_type_label: "Sewer Disconnection Permit",
          use_desc: "BLD DEMO/SEWER DISC",
          buyer: "ROBERT CAMACHO",
        },
      ],
      dshs_notifications: [
        {
          notification_number: "2026005825",
          work_type: "Abatement",
          filing_date: "2026-10-20",
          start_date: "2026-10-27",
          end_date: "2026-11-06",
          facility_owner: "OB TEXAS PROPERTIES LLC",
          abatement_contractor: "AAR INCORPORATED",
          print_action_url:
            "https://vo.ras.dshs.state.tx.us/datamart/notificationPrintAction.do?licId=1234",
        },
      ],
    },
    {
      reviewer: "Emily",
      nowIso: "2026-10-29T16:00:00Z",
      source: "permit_watch_deeplink",
    }
  );
  assert.equal(promotedRawAlert.hcadAccount, "0152300000001, 0152300000002");
  assert.deepEqual(promotedRawAlert.secondaryAddresses, ["530 Pierce St."]);
  assert.equal(promotedRawAlert.humanEdited, true);
  assert.equal(promotedRawAlert.keyDates.asbestosAbatementWindow, "2026-10-27 to 2026-11-06");
  assert.ok(promotedRawAlert.searchTerms.entities.includes("ROBERT CAMACHO"));
  assert.ok(promotedRawAlert.searchTerms.entities.includes("OB TEXAS PROPERTIES LLC"));
  const rawChk14 = findChecklistRow(promotedRawAlert.checklist, "chk-14");
  const rawChk15 = findChecklistRow(promotedRawAlert.checklist, "chk-15");
  assert.equal(rawChk14.status, "Flagged");
  assert.equal(rawChk14.humanStatusEdited, true);
  assert.equal(rawChk14.statusChangedAt, "2026-10-29T16:00:00Z");
  assert.equal(rawChk15.status, "Flagged");
  assert.equal(rawChk15.humanStatusEdited, true);
  assert.equal(rawChk15.statusChangedAt, "2026-10-29T16:00:00Z");
  assert.equal(
    rawChk15.additionalResourceUrl,
    "https://vo.ras.dshs.state.tx.us/datamart/notificationPrintAction.do?licId=1234"
  );

  // 5. buildEventAtlasMapHref honors permitMeta.lat/lng even when top-level ev.lat/lng are null
  const nullTopLevelHref = buildEventAtlasMapHref({
    kind: "permit_coh_alert",
    stream: "permits",
    hcad: "0410170020150",
    lat: null,
    lng: null,
    permitMeta: { lat: 29.7531, lng: -95.4082 },
  });
  assert.ok(
    nullTopLevelHref.includes("lat=29.753100&lng=-95.408200&z=18"),
    `Expected permitMeta coords in map hash when ev.lat is null, got ${nullTopLevelHref}`
  );

  // 6. isNotableNewCycleItem screens out routine modern consent plats while retaining DPVs/notices/historic items
  assert.equal(
    isNotableNewCycleItem(
      {
        app: "2026-9001",
        section: "A. Consent",
        type: "C2",
        _c: { group: "check" },
        atlas: { earliestYearBuilt: 2015, hasActivePermit: false, designations: [] },
      },
      {}
    ),
    false,
    "Routine post-1976 consent subdivision without permit/designation should be filtered on brand-new cycles"
  );
  assert.equal(
    isNotableNewCycleItem(
      {
        app: "2026-9002",
        section: "A. Consent",
        type: "C2",
        _c: { group: "check" },
        atlas: { earliestYearBuilt: 1924, hasActivePermit: false, designations: [] },
      },
      {}
    ),
    true,
    "Pre-1941 historic parcel must be surfaced on brand-new cycles"
  );
}

console.log("  All Activity Log & Morning Briefing tests passed!");
