import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  excelSerialToIso,
  formatShortUsDate,
  formatLongUsDate,
  parseHcadAccountList,
  resolveMultiParcelBuildingTier,
  classifyPlatItem,
  buildOccurrenceKey,
  parseOccurrenceKey,
  normalizeContinuityPlatName,
  buildContinuityKeys,
  linkCrossCycleHistory,
  extractHearingDateIso,
  hasHumanDossierContributions,
} from "../../app/js/platMonitorHelpers.js";

import {
  REMOVED_TIER3_TOMBSTONE,
  simplifySearchAddress,
  extractClerkLegalCopyTerms,
  escapeHtmlText,
  escapeXmlText,
  sanitizeExternalUrl,
  sanitizeExternalLink,
  sanitizeExternalLinksList,
  buildDefaultChecklist,
  findChecklistRow,
  buildAssistedPortalPillsModel,
  normalizeWatchProperty,
  serializeWatchPropertyForSync,
  reconcileWatchlistProperty,
  syncPlatTierToWatchlist,
  sortWatchedProperties,
  parseCsvRfc4180,
  parseCloudSheetSyncRows,
  normalizeDondbHcadId,
  pointInGeoJsonGeometry,
  parseAndMatchDangerousBuildingsCsv,
  buildDangerousBuildingsNotificationText,
} from "../../app/js/watchlistSyncHelpers.js";

import {
  HcadViewportParcelClient,
  normalizeHcad13,
  quantizeViewportBbox,
  collectMinBuildingYearsByHcad,
  enrichParcelFeature,
  buildUnifiedParcelCollection,
} from "../../app/js/hcadViewportParcels.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO_ROOT = path.resolve(__dirname, "..", "..");

test("UT-1: excelSerialToIso floors fractional 2:30 PM meeting serials without +1 day drift", () => {
  assert.equal(excelSerialToIso(46296.60417), "2026-10-01");
  assert.equal(excelSerialToIso(46310.60417), "2026-10-15");
  assert.equal(excelSerialToIso("46310.6041666667"), "2026-10-15");
  assert.equal(excelSerialToIso(46324.60417), "2026-10-29");
  assert.equal(excelSerialToIso(46310.0), "2026-10-15");
  assert.equal(excelSerialToIso(46310.99999), "2026-10-15");
  assert.equal(excelSerialToIso("10/15/2026"), "2026-10-15");
  assert.equal(excelSerialToIso("2026-10-29"), "2026-10-29");
  assert.equal(excelSerialToIso("TBD"), "");
  assert.equal(excelSerialToIso(""), "");
  assert.equal(excelSerialToIso(null), "");
  assert.equal(excelSerialToIso(NaN), "");
  assert.equal(formatShortUsDate("2026-10-15"), "10/15/2026");
  assert.equal(formatLongUsDate("2026-11-12", true), "Nov 12, 2026");
});

test("UT-2: Multi-building and multi-HCAD construction year aggregation picks min(year_built >= 1836)", () => {
  const parsed = parseHcadAccountList("152300000001; 0660450130001, 0660450130016");
  assert.deepEqual(parsed.hcadIds, [
    "0152300000001",
    "0660450130001",
    "0660450130016",
  ]);

  // Multi-building single parcel with 0/unknown + 1928 historic + 2005 garage -> 1928 (Tier 1)
  const res1928 = resolveMultiParcelBuildingTier([
    { hcad: "0410170020150", year_built: 0, all_years: [0, 1928, 2005] },
  ]);
  assert.equal(res1928.oldestYearBuilt, 1928);
  assert.equal(res1928.suggestedTier, "1");

  // Multi-building 1938 parcel: Tier 2 unflagged, Tier 1 when paired with variance/hearing flag
  const res1938Unflagged = resolveMultiParcelBuildingTier([
    { hcad: "0410170020151", year_built: 0, all_years: [0, 1938, 2005] },
  ]);
  assert.equal(res1938Unflagged.oldestYearBuilt, 1938);
  assert.equal(res1938Unflagged.suggestedTier, "2");

  const res1938Variance = resolveMultiParcelBuildingTier(
    [{ hcad: "0410170020151", year_built: 0, all_years: [0, 1938, 2005] }],
    { flags: ["variance"] }
  );
  assert.equal(res1938Variance.oldestYearBuilt, 1938);
  assert.equal(res1938Variance.suggestedTier, "1");

  // Pre-1836 bogus year ignored; 1968 retained -> Tier 2
  const res1968 = resolveMultiParcelBuildingTier([
    { hcad: "0660450130001", year_built: 1800, all_years: [1800, 1968] },
  ]);
  assert.equal(res1968.oldestYearBuilt, 1968);
  assert.equal(res1968.suggestedTier, "2");

  // Vacant [0, 0] -> oldestYearBuilt null/0 (no bogus 0 minimum)
  const resVacant = resolveMultiParcelBuildingTier([
    { hcad: "0999990000001", year_built: 0, all_years: [0, 0] },
  ]);
  assert.ok(!resVacant.oldestYearBuilt, "Vacant parcel must not report 0 or pre-1836 year");

  // Post-1976 modern parcel [1985, 2012] -> 1985 (empty suggestedTier when unflagged)
  const resModern = resolveMultiParcelBuildingTier([
    { hcad: "0888880000001", year_built: 2012, all_years: [1985, 2012] },
  ]);
  assert.equal(resModern.oldestYearBuilt, 1985);
  assert.equal(resModern.suggestedTier, "");

  // Amerimex Motors multi-HCAD (1966 + 1964 across 2 accounts) -> 1964 (Tier 2)
  const amerimex = resolveMultiParcelBuildingTier([
    { hcad: "0660450130001", year_built: 1966, all_years: [1966] },
    { hcad: "0660450130016", year_built: 1964, all_years: [1964, 1978] },
  ]);
  assert.equal(amerimex.oldestYearBuilt, 1964);
  assert.equal(amerimex.suggestedTier, "2");

  // Curated overrides take precedence over raw HCAD year
  const withOverride = resolveMultiParcelBuildingTier(
    [{ hcad: "0152300000001", year_built: 1955, all_years: [1955] }],
    {
      curatedOverrides: {
        "0152300000001": { year_built: 1894, landmark_name: "John Henry Kirby Mansion" },
      },
    }
  );
  assert.equal(withOverride.oldestYearBuilt, 1894);
  assert.equal(withOverride.suggestedTier, "1");
});

test("UT-3: classifyPlatItem screens in blank-prevSub items with variances, hearings, DPVs, institutions, pre-1976 structures, permits, or designations", () => {
  const baseBlankPrevSub = {
    name: "Greenfield Reserve Sec 1",
    app: "2026-9900",
    type: "Class 2 subdivision plat (C2)",
    special: "",
    section: "A. Consent",
    city: "City",
    prevSub: "",
    superN: "ELDORADO / OATES PRAIRIE",
    hd: "",
    landUse: "Commercial",
    lots: "1",
    atlas: { earliestYearBuilt: 0, designations: [], hasActivePermit: false },
  };

  // Pure greenfield blank-prevSub Consent item with no triggers -> screened out with explicit reason
  const pureOut = classifyPlatItem(baseBlankPrevSub);
  assert.equal(pureOut.group, "out");
  assert.ok(
    typeof pureOut.reason === "string" && pureOut.reason.length > 0,
    "Screened-out items must expose human-readable screen-out reason"
  );
  assert.match(pureOut.reason, /no previous subdivision/i);

  // ETJ item -> group 'etj'
  const etjOut = classifyPlatItem({ ...baseBlankPrevSub, city: "ETJ" });
  assert.equal(etjOut.group, "etj");

  // Manual overrideScreenOut forces screened-out item back into 'check'
  const overridden = classifyPlatItem(baseBlankPrevSub, { overrideScreenOut: true });
  assert.equal(overridden.group, "check");

  // Each of these blank-prevSub exemptions MUST screen IN ('check' or 'designated'), never 'out':
  const mustScreenInCases = [
    {
      label: "Section D Variance with blank prevSub (Amerimex Motors pattern)",
      patch: { section: "D. Variances", special: "Variance" },
    },
    {
      label: "Section IV Establish Public Hearing / C3N with blank prevSub",
      patch: {
        section: "IV. Establish Public Hearing Date (Nov 12, 2026)",
        type: "Class 3 Replat with Public Hearing Notice (C3N)",
      },
    },
    {
      label: "Section K Development Plat Variance (DPV) with blank prevSub",
      patch: {
        app: "DPV-26073533",
        section: "K. Development Plat Variances",
        type: "Development Plat Variance (DPV)",
      },
    },
    {
      label: "Reconsideration of Requirement with blank prevSub",
      patch: { section: "F. Reconsideration of Requirements" },
    },
    {
      label: "Institutional church/chapel replat with blank prevSub",
      patch: { name: "First Baptist Chapel Reserve" },
    },
    {
      label: "Pre-1976 structure (1964) with blank prevSub",
      patch: { atlas: { earliestYearBuilt: 1964, designations: [], hasActivePermit: false } },
    },
    {
      label: "Active demolition/building permit alert with blank prevSub",
      patch: { atlas: { earliestYearBuilt: 0, designations: [], hasActivePermit: true } },
    },
    {
      label: "Landmark / Good Brick designation with blank prevSub",
      patch: {
        atlas: {
          earliestYearBuilt: 0,
          designations: ["Good Brick Award"],
          hasActivePermit: false,
        },
      },
    },
    {
      label: "City Historic District item",
      patch: { hd: "Houston Heights Historic District East" },
      expectedGroup: "designated",
    },
  ];

  for (const tc of mustScreenInCases) {
    const res = classifyPlatItem({ ...baseBlankPrevSub, ...tc.patch });
    const expected = tc.expectedGroup || "check";
    assert.equal(res.group, expected, `${tc.label} expected group '${expected}', got '${res.group}'`);
  }
});

test("UT-4: Hearing date extraction, noticePdf separation, and strict cross-cycle continuity keys", () => {
  assert.equal(
    extractHearingDateIso("Establish a public hearing date of Thursday, November 12, 2026"),
    "2026-11-12"
  );
  assert.equal(extractHearingDateIso("Public Hearing: October 29, 2026"), "2026-10-29");

  const occ = buildOccurrenceKey("2026-10-29", "2026-1659");
  assert.equal(occ, "2026-10-29::2026-1659");
  assert.deepEqual(parseOccurrenceKey(occ), { cycleKey: "2026-10-29", appNo: "2026-1659" });

  // Section numbers must be preserved so Sec 1 and Sec 2 never collide
  const sec1Name = normalizeContinuityPlatName("Oak Estates Sec 1 partial replat no 1");
  const sec2Name = normalizeContinuityPlatName("Oak Estates Sec 2 partial replat no 1");
  assert.notEqual(sec1Name, sec2Name);

  // Generic standalone names must NOT emit a name-based continuity key
  const genericKeys = buildContinuityKeys({ name: "Reserve A", app: "2026-9001", hcad: "" });
  assert.equal(
    genericKeys.some((k) => k.startsWith("name:")),
    false,
    "Generic standalone plat names must not produce false-merge name continuity keys"
  );

  // Link deferred Amerimex Motors across 10/15 -> 10/29 while keeping Sec 1 vs Sec 2 separate
  const cyclesMap = {
    "2026-10-15": {
      items: [
        {
          app: "2026-1610",
          name: "Amerimex Motors",
          hcad: "0660450130001; 0660450130016",
          section: "D. Variances",
        },
        { app: "2026-1611", name: "Oak Estates Sec 1", hcad: "", section: "A. Consent" },
      ],
      reviews: {
        "2026-1610": { tier: "2", why: "Deferred from Oct 15 variance review" },
      },
    },
    "2026-10-29": {
      items: [
        {
          app: "2026-1659",
          name: "Amerimex Motors partial replat no 1",
          hcad: "0660450130001; 0660450130016",
          section: "D. Variances",
        },
        { app: "2026-1660", name: "Oak Estates Sec 2", hcad: "", section: "A. Consent" },
      ],
      reviews: {},
    },
  };
  linkCrossCycleHistory(cyclesMap, ["2026-10-15", "2026-10-29"]);

  const amerimexOct29 = cyclesMap["2026-10-29"].items[0];
  assert.ok(
    Array.isArray(amerimexOct29.priorCycles) && amerimexOct29.priorCycles.length === 1,
    "Amerimex Motors on 10/29 must link back to its 10/15 prior appearance"
  );
  assert.equal(amerimexOct29.priorCycles[0].cycleKey, "2026-10-15");
  assert.equal(amerimexOct29.priorCycles[0].app, "2026-1610");

  const oakSec2Oct29 = cyclesMap["2026-10-29"].items[1];
  assert.equal(
    (oakSec2Oct29.priorCycles || []).length,
    0,
    "Oak Estates Sec 2 must not false-match Oak Estates Sec 1"
  );

  // Verify live plat_agenda_monitor.json Montrose Residences contract
  const platJson = JSON.parse(
    fs.readFileSync(path.join(REPO_ROOT, "app", "public", "data", "plat_agenda_monitor.json"), "utf8")
  );
  const oct29Items = platJson.cycles?.["2026-10-29"]?.items || [];
  const montroseRes = oct29Items.find((i) =>
    String(i.name || "").toLowerCase().includes("montrose residences")
  );
  assert.ok(montroseRes, "Montrose Residences must exist on the 2026-10-29 cycle");
  assert.equal(
    montroseRes.app,
    "2026-1585",
    "Montrose Residences must be C3N Subdivision Plat 2026-1585, not misclassified as DPV-2026-1585"
  );
  assert.equal(montroseRes.pdf, "", "Notice-only item Montrose Residences must have empty plat pdf");
  assert.match(montroseRes.noticePdf || "", /2026-1585_Montrose-Residences_PH\.pdf$/);
  assert.equal(montroseRes.hearingDateIso, "2026-11-12");
});

test("UT-5: simplifySearchAddress handles Houston street suffixes, units, bare condo numbers, and Saint/Avenue names", () => {
  const cases = [
    ["3827 Broadway St", "3827 Broadway"],
    ["2000 Smith St.", "2000 Smith"],
    ["530 Pierce St.", "530 Pierce"],
    ["910 Louisiana St Ste 400", "910 Louisiana"],
    ["909 TEXAS AVE 308", "909 TEXAS"],
    ["1 MAIN ST 562", "1 MAIN"],
    ["2100 St Emanuel St", "2100 St Emanuel"],
    ["1400 St Joseph Pkwy", "1400 St Joseph"],
    ["1200 Ave A", "1200 Ave A"],
    ["6500 Avenue J", "6500 Avenue J"],
    ["400 Park Way", "400 Park"],
  ];
  for (const [input, expected] of cases) {
    assert.equal(
      simplifySearchAddress(input),
      expected,
      `simplifySearchAddress(${JSON.stringify(input)}) mismatch`
    );
  }
});

test("UT-6 & MOD-2: extractClerkLegalCopyTerms delegates to clerkSearchHelpers.js across Lot/Block/Tract/Reserve/Abstract formats", () => {
  const hadley = extractClerkLegalCopyTerms("LT 12 BLK 34 HADLEY ADDITION");
  assert.equal(hadley.block, "34");
  assert.equal(hadley.subdivisionKeyword, "HADLEY");

  const kirby = extractClerkLegalCopyTerms("TRS 1-16 BLK 417 SSBB");
  assert.equal(kirby.block, "417");
  assert.equal(kirby.subdivisionKeyword, "SSBB");

  const norhill = extractClerkLegalCopyTerms("RES A BLK 1 NORTH NORHILL");
  assert.equal(norhill.block, "1");
  assert.equal(norhill.subdivisionFull, "NORTH NORHILL");
  assert.equal(norhill.subdivisionKeyword, "NORHILL");

  const abst1 = extractClerkLegalCopyTerms("ABST 696 O SMITH");
  assert.equal(abst1.block, "");
  assert.equal(abst1.subdivisionKeyword, "");
  assert.equal(abst1.abstract, "696");

  const abst2 = extractClerkLegalCopyTerms("TR 2A ABST 123 J AUSTIN");
  assert.equal(abst2.block, "");
  assert.equal(abst2.subdivisionKeyword, "");
  assert.equal(abst2.abstract, "123");

  const abst3 = extractClerkLegalCopyTerms("5.432 AC ABST 291 H & T C R R CO");
  assert.equal(abst3.block, "");
  assert.equal(abst3.subdivisionKeyword, "");
  assert.equal(abst3.abstract, "291");

  // MOD-2: Verify watchlistSyncHelpers.js imports directly from ./clerkSearchHelpers.js
  const wlSrc = fs.readFileSync(
    path.join(REPO_ROOT, "app", "js", "watchlistSyncHelpers.js"),
    "utf8"
  );
  assert.match(wlSrc, /from\s+["']\.\/clerkSearchHelpers\.js(?:\?v=[^"']+)?["']/);
});

test("UT-7: buildAssistedPortalPillsModel routes portal-specific copy payloads with zero street-address fallthrough on #3, #4, #7, #8, #9", () => {
  const kirbyProp = normalizeWatchProperty({
    id: "prop-kirby-mansion",
    propertyName: "John Henry Kirby Mansion",
    platProjectName: "Smith Street Plaza",
    address: "2000 Smith St.",
    secondaryAddresses: ["530 Pierce St."],
    hcadAccount: "0152300000001",
    legalDescription: "TRS 1-16 BLK 417 SSBB",
    ownerOfRecord: "OB Texas Properties LLC",
    buyerRelatedEntities: "Smith St C Store LLC",
    designFirm: "Heights Engineering",
    lng: -95.374833,
    lat: 29.751432,
    searchTerms: {
      entities: [
        "Smith St C Store LLC",
        "OB Texas Properties LLC",
        "BTCM National Retail Operations LLC",
        "Kirby Block Holdings LLC",
      ],
      dbas: ["Brake Time"],
      officers: ["Omair Bashir"],
      legalKeywords: ["417", "SSBB"],
      altAddresses: ["530 Pierce St."],
      projectNumbers: ["26052089", "TABS2026022839"],
    },
  });

  const nonAddressChecks = ["chk-03", "chk-04", "chk-07", "chk-08", "chk-09"];
  for (const cid of nonAddressChecks) {
    const row = findChecklistRow(kirbyProp.checklist, cid);
    const pills = buildAssistedPortalPillsModel(kirbyProp, row);
    assert.ok(pills.length > 0, `Expected non-empty assisted pills for ${cid}`);
    for (const pill of pills) {
      if (pill.kind === "copy") {
        assert.notEqual(
          pill.copyText,
          "2000 Smith",
          `${cid} must never auto-copy simplified street address '2000 Smith'`
        );
        assert.notEqual(
          pill.copyText,
          "2000 Smith St.",
          `${cid} must never auto-copy raw street address '2000 Smith St.'`
        );
      }
    }
  }

  // Specific positive assertions per portal check (including 4th+ snowball entities without .slice(0, 3) truncation)
  const p3 = buildAssistedPortalPillsModel(kirbyProp, findChecklistRow(kirbyProp.checklist, "chk-03"));
  assert.ok(p3.some((p) => p.kind === "copy" && p.copyText === "417"));
  assert.ok(p3.some((p) => p.kind === "copy" && p.copyText === "SSBB"));

  const p4 = buildAssistedPortalPillsModel(kirbyProp, findChecklistRow(kirbyProp.checklist, "chk-04"));
  for (const expectedTerm of [
    "Smith St C Store LLC",
    "OB Texas Properties LLC",
    "BTCM National Retail Operations LLC",
    "Kirby Block Holdings LLC",
    "Omair Bashir",
    "Brake Time",
  ]) {
    assert.ok(
      p4.some((p) => p.kind === "copy" && p.copyText === expectedTerm),
      `Expected Check #4 copy pill for snowball term '${expectedTerm}'`
    );
  }
  assert.ok(p4.some((p) => p.label.includes("Bizapedia")));

  const p7 = buildAssistedPortalPillsModel(kirbyProp, findChecklistRow(kirbyProp.checklist, "chk-07"));
  assert.ok(p7.some((p) => p.kind === "copy" && p.copyText === "Brake Time"));
  assert.ok(p7.some((p) => p.kind === "copy" && p.copyText === "Kirby Block Holdings LLC"));
  assert.ok(p7.some((p) => p.kind === "copy" && p.copyText === "Omair Bashir"));

  const p8 = buildAssistedPortalPillsModel(kirbyProp, findChecklistRow(kirbyProp.checklist, "chk-08"));
  assert.ok(p8.some((p) => p.kind === "copy" && p.copyText === "26052089"));
  assert.ok(p8.some((p) => p.kind === "action" && p.action === "open-govqa-modal"));

  const p10 = buildAssistedPortalPillsModel(kirbyProp, findChecklistRow(kirbyProp.checklist, "chk-10"));
  assert.ok(p10.some((p) => p.kind === "copy" && (p.copyText === "2000 Smith" || p.copyText === "530 Pierce")));
  assert.ok(p10.some((p) => p.kind === "copy" && p.copyText === "Kirby Block Holdings LLC"));

  const p11 = buildAssistedPortalPillsModel(kirbyProp, findChecklistRow(kirbyProp.checklist, "chk-11"));
  assert.ok(p11.some((p) => p.kind === "copy" && p.copyText === "2000 Smith" && (p.openUrl || "").includes("novusagenda.com")));

  const p12 = buildAssistedPortalPillsModel(kirbyProp, findChecklistRow(kirbyProp.checklist, "chk-12"));
  assert.ok(p12.some((p) => p.kind === "copy" && p.copyText === "Smith St C Store LLC" && (p.openUrl || "").includes("tceq.texas.gov")));

  const p13 = buildAssistedPortalPillsModel(kirbyProp, findChecklistRow(kirbyProp.checklist, "chk-13"));
  assert.ok(p13.some((p) => p.kind === "action" && p.action === "open-dondb-modal"));

  const p14 = buildAssistedPortalPillsModel(kirbyProp, findChecklistRow(kirbyProp.checklist, "chk-14"));
  assert.ok(p14.some((p) => p.kind === "copy" && p.copyText === "2000 Smith"));
  assert.ok(p14.some((p) => p.kind === "copy" && p.copyText === "530 Pierce"));
});

test("UT-8: Dangerous Buildings DONDB*.csv RFC-4180 parser, 13-digit HCAD zero-padding, scientific-notation warning, address_shards, and overlays PIP", async () => {
  assert.equal(normalizeDondbHcadId("152300000001").hcad, "0152300000001");
  const sci = normalizeDondbHcadId("1.523E+11");
  assert.equal(sci.hcad, "");
  assert.match(sci.warning, /Scientific notation/i);

  const poly = {
    type: "Polygon",
    coordinates: [
      [
        [-95.40, 29.74],
        [-95.36, 29.74],
        [-95.36, 29.77],
        [-95.40, 29.77],
        [-95.40, 29.74],
      ],
    ],
  };
  assert.equal(pointInGeoJsonGeometry(-95.38, 29.75, poly), true);
  assert.equal(pointInGeoJsonGeometry(-95.50, 29.75, poly), false);

  const sampleCsv = [
    "CaseNumber,Parcel.ParcelID,Parcel.ParcelName,Parcel.Section,Parcel.Area.Code,ProcessState.Code",
    'DB-2026-0001,152300000001,"2000   SMITH ST, HOUSTON",SEC-1,CENTRAL,OPEN_HEARING',
    'DB-2026-0002,1.523E+11,"1234   HADLEY   ST",SEC-2,CENTRAL,INSPECTION',
    'DB-2026-0003,,"4567   MODERN   PKWY",SEC-3,OUTER,CLOSED',
    'DB-2026-0004,0555550010001,"789   MIDCENTURY   AVE",SEC-4,INNER,NOTICE_SENT',
  ].join("\r\n");

  const grid = parseCsvRfc4180(sampleCsv);
  assert.equal(grid.length, 5);
  assert.equal(grid[1][2], "2000   SMITH ST, HOUSTON");

  const result = await parseAndMatchDangerousBuildingsCsv(sampleCsv, {
    watchedProperties: [
      {
        id: "prop-kirby-mansion",
        propertyName: "John Henry Kirby Mansion",
        address: "2000 Smith St.",
        hcadAccount: "0152300000001",
      },
    ],
    curatedOverrides: {
      "0555550010001": { year_built: 1954, address: "789 MIDCENTURY AVE" },
    },
    loadAddressShard: async (prefix2) => {
      if (prefix2 === "12") {
        return {
          "HADLEY ST": [["1234", -95.38, 29.75, 1922, "0222220010005", ""]],
        };
      }
      if (prefix2 === "45") {
        return {
          "MODERN PKWY": [["4567", -95.55, 29.85, 2008, "0999990010009", ""]],
        };
      }
      return {};
    },
    overlays: {
      historic_districts: {
        type: "FeatureCollection",
        features: [
          {
            type: "Feature",
            properties: { name: "Synthetic Midtown Historic District" },
            geometry: poly,
          },
        ],
      },
    },
  });

  assert.equal(result.totalRows, 4);
  assert.equal(result.warnings.length, 1);
  assert.match(result.warnings[0].warning, /Scientific notation/);
  assert.equal(result.tier1.length, 2, "Kirby Mansion (12-digit padded) and 1234 Hadley (1922 + HD PIP) must match Tier 1");
  assert.equal(result.tier2.length, 1, "1954 midcentury property must match Tier 2");
  assert.equal(result.nonHistoric.length, 1, "2008 outer-loop property must classify as nonHistoric");

  const summary = buildDangerousBuildingsNotificationText([...result.tier1, ...result.tier2]);
  assert.match(summary, /DB-2026-0001/);
  assert.match(summary, /0152300000001/);
  assert.match(summary, /DB-2026-0004/);
});

test("UT-9: URL scheme sanitization and HTML/XML escaping block XSS and XML corruption", () => {
  assert.equal(sanitizeExternalUrl("javascript:alert(1)"), "");
  assert.equal(sanitizeExternalUrl(" JAVASCRIPT:alert(document.cookie) "), "");
  assert.equal(sanitizeExternalUrl("data:text/html,<script>alert(1)</script>"), "");
  assert.equal(sanitizeExternalUrl("vbscript:msgbox(1)"), "");
  assert.equal(sanitizeExternalUrl("file:///etc/passwd"), "");
  assert.equal(
    sanitizeExternalUrl(
      "https://vo.ras.dshs.state.tx.us/datamart/notificationPrintAction.do?licId=2451374&mode=print"
    ),
    "https://vo.ras.dshs.state.tx.us/datamart/notificationPrintAction.do?licId=2451374&mode=print"
  );

  const cleanedList = sanitizeExternalLinksList([
    { label: "Attack", url: "javascript:evil()" },
    {
      label: 'DSHS & Co. <Report>',
      url: "https://vo.ras.dshs.state.tx.us/datamart/notificationPrintAction.do?licId=2451374&x=1",
    },
  ]);
  assert.equal(cleanedList.length, 1);
  assert.equal(
    escapeHtmlText(cleanedList[0].label),
    "DSHS &amp; Co. &lt;Report&gt;"
  );
  assert.equal(
    escapeXmlText(cleanedList[0].url),
    "https://vo.ras.dshs.state.tx.us/datamart/notificationPrintAction.do?licId=2451374&amp;x=1"
  );
  assert.equal(sanitizeExternalLink({ label: "Bad", url: "data:text/plain,hi" }), null);
});

test("UT-11 & SCH-2: 3-way Watchlist reconciliation preserves [AUTO-SWEEP], protects seeded searchTerms from legacy cloud rows, respects explicit deletions, and guards Tier 3 downgrades", () => {
  // 1. Legacy cloud row WITHOUT searchTerms or driveLinks keys must NOT wipe staticProp's seeded searchTerms or driveLinks
  const staticKirby = {
    id: "prop-kirby-mansion",
    propertyName: "John Henry Kirby Mansion",
    address: "2000 Smith St.",
    hcadAccount: "0152300000001",
    tier: "1",
    tierConfirmed: true,
    lastAutoSweepAt: "2026-10-27T12:00:00Z",
    searchTerms: {
      entities: ["Smith St C Store LLC", "OB Texas Properties LLC"],
      dbas: ["Brake Time"],
      officers: ["Omair Bashir"],
      legalKeywords: ["417", "SSBB"],
      altAddresses: ["530 Pierce St."],
      projectNumbers: ["26052089", "26090093"],
    },
    driveLinks: [{ label: "Deed PDF", url: "https://drive.google.com/file/d/example1" }],
    checklist: buildDefaultChecklist("2000 Smith St.").map((r) =>
      r.id === "chk-14"
        ? {
            ...r,
            status: "Flagged",
            lastChecked: "2026-10-27",
            checks: [
              {
                date: "2026-10-27",
                source: "auto-sweep",
                result: "[AUTO-SWEEP] COH Permit #26090093 BLD DEMO/SEWER DISC at 2000 Smith St",
              },
            ],
          }
        : r
    ),
  };

  const legacyCloudSnapshot = {
    id: "prop-kirby-mansion",
    propertyName: "John Henry Kirby Mansion",
    address: "2000 Smith St.",
    hcadAccount: "0152300000001",
    architectEngineer: "Heights Engineering (Munir Ishaq, P.E.)",
    notes: "Staff phone call logged Oct 23.",
    updatedAt: "2026-10-23T18:00:00Z",
    // Intentionally omits searchTerms and driveLinks/documents (legacy Oct 23 schema)
    checklist: buildDefaultChecklist("2000 Smith St.").map((r) =>
      r.id === "chk-02"
        ? {
            ...r,
            status: "Complete",
            lastChecked: "2026-10-23",
            checks: [{ date: "2026-10-23", result: "Called broker; left voicemail" }],
          }
        : r
    ),
  };

  const mergedLegacy = reconcileWatchlistProperty(staticKirby, legacyCloudSnapshot, null);
  assert.equal(mergedLegacy.designFirm, "Heights Engineering (Munir Ishaq, P.E.)");
  assert.equal(mergedLegacy.architectEngineer, "Heights Engineering (Munir Ishaq, P.E.)");
  assert.ok(
    mergedLegacy.searchTerms.entities.includes("Smith St C Store LLC"),
    "Legacy cloud row without searchTerms key must NOT wipe static seeded snowball entities"
  );
  assert.ok(
    mergedLegacy.searchTerms.dbas.includes("Brake Time"),
    "Legacy cloud row without searchTerms key must NOT wipe static seeded snowball DBAs"
  );
  assert.equal(
    mergedLegacy.driveLinks.length,
    1,
    "Legacy cloud row without driveLinks/documents key must NOT wipe static Drive links"
  );
  assert.equal(
    findChecklistRow(mergedLegacy.checklist, "chk-02").status,
    "Complete",
    "Manual Complete status on chk-02 from cloud snapshot must survive"
  );
  const mergedChk14 = findChecklistRow(mergedLegacy.checklist, "chk-14");
  assert.equal(mergedChk14.status, "Flagged");
  assert.ok(
    mergedChk14.checks.some((c) => c.result.includes("26090093")),
    "Fresh [AUTO-SWEEP] finding on chk-14 must survive older cloud checklist snapshot"
  );

  // 2. Explicit human deletion of a snowball term & Drive link with newer timestamp MUST NOT resurrect
  const localExplicitDelete = {
    ...serializeWatchPropertyForSync(mergedLegacy, "Emily Ardoin", "2026-10-27T13:30:00Z"),
    searchTerms: {
      entities: ["OB Texas Properties LLC"],
      dbas: [],
      officers: ["Omair Bashir"],
      legalKeywords: ["417", "SSBB"],
      altAddresses: ["530 Pierce St."],
      projectNumbers: ["26052089", "26090093"],
    },
    searchTermsUpdatedAt: "2026-10-27T13:30:00Z",
    driveLinks: [],
    documents: [],
    driveLinksUpdatedAt: "2026-10-27T13:30:00Z",
  };
  const mergedAfterDelete = reconcileWatchlistProperty(
    staticKirby,
    legacyCloudSnapshot,
    localExplicitDelete
  );
  assert.deepEqual(mergedAfterDelete.searchTerms.entities, ["OB Texas Properties LLC"]);
  assert.deepEqual(mergedAfterDelete.searchTerms.dbas, []);
  assert.deepEqual(mergedAfterDelete.driveLinks, []);

  // 3. Tier 3 downgrade soft-archives when human contributions exist, and hard-removes when untouched
  // Include real pipeline-seeded legalDescription, chk-09 plat-seed with PDF links, and [AUTO-SWEEP] links
  const untouchedPlat = normalizeWatchProperty({
    id: "plat-2026-1700",
    sourcePlatApp: "2026-1700",
    address: "100 Blank Ave",
    legalDescription: "LT 1 BLK 2 MONTROSE",
    tier: "2",
    tierConfirmed: true,
    notes: "Auto-seeded plat review note",
    checklist: buildDefaultChecklist("100 Blank Ave").map((r) => {
      if (r.id === "chk-09") {
        return {
          ...r,
          status: "Flagged",
          lastChecked: "2026-10-29",
          checks: [
            {
              date: "2026-10-29",
              result: "Confirmed from 2026-10-29 Planning Commission Agenda (2026-1700 - C3N): Variance",
              source: "plat-seed",
              links: [{ label: "Public Hearing Notice PDF", url: "https://www.houstontx.gov/notice.pdf" }],
            },
          ],
        };
      }
      if (r.id === "chk-10") {
        return {
          ...r,
          status: "Flagged",
          lastAutoSweepAt: "2026-10-27T12:00:00Z",
          checks: [
            {
              date: "2026-08-21",
              discoveredAt: "2026-10-27",
              source: "auto-sweep",
              adverse: true,
              agencyRecordId: "tabs:TABS2026028478",
              result: "[AUTO-SWEEP] TDLR TABS #TABS2026028478: $1,000,000 New Construction",
              links: [{ label: "TDLR TABS2026028478", url: "https://www.tdlr.texas.gov/TABS/Search/Project/TABS2026028478" }],
            },
          ],
        };
      }
      return r;
    }),
  });
  assert.equal(untouchedPlat.appNo, "2026-1700", "sourcePlatApp must hydrate appNo");
  assert.equal(hasHumanDossierContributions(untouchedPlat), false);
  const removedRes = syncPlatTierToWatchlist([untouchedPlat], {
    appNo: "2026-1700",
    newTier: "3",
    nowIso: "2026-10-27T14:00:00Z",
  });
  assert.equal(removedRes.action, "removed");
  assert.equal(removedRes.watchedProperties.length, 0);

  // Tombstone blocks cloud/static resurrection until re-promoted
  const tombstoneProp = normalizeWatchProperty({
    ...untouchedPlat,
    tier: "3",
    tierConfirmed: false,
    trackerStatus: REMOVED_TIER3_TOMBSTONE,
    updatedAt: "2026-10-27T14:05:00Z",
  });
  assert.equal(
    reconcileWatchlistProperty(untouchedPlat, untouchedPlat, tombstoneProp),
    null,
    "Newer Removed (Tier 3) tombstone must prevent stale cloud/static snapshots from resurrecting"
  );

  // 4. Backdated adverse [AUTO-SWEEP] hit escalates To Do -> Flagged; manual Complete persists; newer sweep re-escalates
  const staticBackdatedAdverse = normalizeWatchProperty({
    id: "plat-DPV-26073533",
    sourcePlatApp: "DPV-26073533",
    address: "801 McGowen St",
    tier: "1",
    lastAutoSweepAt: "2026-10-27T12:00:00Z",
    checklist: buildDefaultChecklist("801 McGowen St").map((r) =>
      r.id === "chk-10"
        ? {
            ...r,
            status: "To Do",
            lastChecked: "",
            lastAutoSweepAt: "2026-10-27T12:00:00Z",
            checks: [
              {
                date: "2026-08-21",
                discoveredAt: "2026-10-27",
                source: "auto-sweep",
                adverse: true,
                agencyRecordId: "tabs:TABS2026028478",
                result: "[AUTO-SWEEP] TDLR TABS #TABS2026028478: $1,000,000 C Store With Gas Station",
              },
            ],
          }
        : r
    ),
  });
  const recInitial = reconcileWatchlistProperty(staticBackdatedAdverse, null, null);
  assert.equal(
    findChecklistRow(recInitial.checklist, "chk-10").status,
    "Flagged",
    "Backdated adverse TDLR TABS filing (2026-08-21 discovered 2026-10-27) must escalate To Do -> Flagged"
  );

  // Staff marks chk-10 Complete on 2026-10-27 -> persists across reconciliation
  const localResolved = normalizeWatchProperty({
    ...recInitial,
    updatedAt: "2026-10-27T15:00:00Z",
    humanEdited: true,
    checklist: recInitial.checklist.map((r) =>
      r.id === "chk-10"
        ? {
            ...r,
            status: "Complete",
            lastChecked: "2026-10-27",
            updatedAt: "2026-10-27T15:00:00Z",
            humanStatusEdited: true,
          }
        : r
    ),
  });
  assert.equal(
    findChecklistRow(localResolved.checklist, "chk-10").humanStatusEdited,
    true,
    "normalizeWatchProperty must preserve row.humanStatusEdited"
  );
  const recAfterComplete = reconcileWatchlistProperty(staticBackdatedAdverse, null, localResolved);
  assert.equal(
    findChecklistRow(recAfterComplete.checklist, "chk-10").status,
    "Complete",
    "Manual Complete status must NOT revert to Flagged on same-day reconciliation"
  );

  // Next-day 2026-10-28 sweep discovers a brand-new adverse filing -> re-escalates Complete -> Flagged
  const staticNextDayAdverse = normalizeWatchProperty({
    ...staticBackdatedAdverse,
    lastAutoSweepAt: "2026-10-28T12:00:00Z",
    checklist: staticBackdatedAdverse.checklist.map((r) =>
      r.id === "chk-10"
        ? {
            ...r,
            lastAutoSweepAt: "2026-10-28T12:00:00Z",
            checks: [
              ...r.checks,
              {
                date: "2026-10-28",
                discoveredAt: "2026-10-28",
                source: "auto-sweep",
                adverse: true,
                agencyRecordId: "tabs:TABS2026099999",
                result: "[AUTO-SWEEP] TDLR TABS #TABS2026099999: $500,000 Canopy Addition",
              },
            ],
          }
        : r
    ),
  });
  const recNextDay = reconcileWatchlistProperty(staticNextDayAdverse, null, localResolved);
  assert.equal(
    findChecklistRow(recNextDay.checklist, "chk-10").status,
    "Flagged",
    "Genuinely new adverse hit discovered after humanAckDate must re-escalate Complete -> Flagged"
  );

  // 5. HCAD normalizer consistency (#2 sub-building suffix & all-zero sentinel rejection) + colliding app:2026-1585 guard + Cloud CSV sync symmetry
  assert.equal(normalizeHcad13("0000000000000"), "");
  assert.equal(normalizeHcad13("0152300000001#2"), "0152300000001");
  assert.deepEqual(parseHcadAccountList("0000000000000, 0152300000001#2").hcadIds, ["0152300000001"]);
  assert.equal(normalizeDondbHcadId("0000000000000").hcad, "");
  assert.equal(normalizeDondbHcadId("0152300000001#2").hcad, "0152300000001");

  const collidingCycles = linkCrossCycleHistory({
    "2026-10-01": {
      items: [{ app: "2026-1585", name: "Montrose Vista Replat No 1", hcad: "0451110000009" }],
      reviews: {},
    },
    "2026-10-29": {
      items: [{ app: "2026-1585", name: "Montrose Residences", hcad: "0231440000010" }],
      reviews: {},
    },
  });
  assert.deepEqual(
    collidingCycles["2026-10-29"].items[0].priorCycles,
    [],
    "Colliding COH reference number 2026-1585 for distinct projects/HCADs must not merge priorCycles"
  );

  const cloudCsv = [
    "timestamp,hcad_num,source_type,source_citation,verified_by,status",
    `2026-10-27T12:00:00Z,PLAT_REV:2026-10-29:2026-1585,PLAT_MONITOR_REVIEW,"${JSON.stringify({
      cycleKey: "2026-10-29",
      app: "2026-1585",
      review: { tier: "1", tierConfirmed: true, updated: "2026-10-27T12:00:00Z" },
    }).replace(/"/g, '""')}",Emily Ardoin,approved`,
    `2026-10-27T14:00:00Z,WATCH_PROP:plat-2026-1700,PROPERTY_WATCHLIST_UPDATE,"${JSON.stringify({
      id: "plat-2026-1700",
      tier: "3",
      trackerStatus: REMOVED_TIER3_TOMBSTONE,
      updatedAt: "2026-10-27T14:00:00Z",
    }).replace(/"/g, '""')}",Emily Ardoin,approved`,
  ].join("\n");
  const parsedCloud = parseCloudSheetSyncRows(cloudCsv);
  assert.equal(parsedCloud.platReviews.length, 1);
  assert.equal(parsedCloud.platReviews[0].appNo, "2026-1585");
  assert.equal(parsedCloud.watchProperties.length, 1);
  assert.equal(parsedCloud.watchProperties[0].isRemovedTombstone, true);

  const researchedPlat = normalizeWatchProperty({
    id: "plat-2026-1659",
    appNo: "2026-1659",
    address: "7115 Harrisburg Blvd",
    tier: "2",
    tierConfirmed: true,
    phPastActions: "Site visit logged; verified 1964 structure on corner parcel.",
    searchTerms: { entities: ["Amerimex Motors LLC"] },
  });
  assert.equal(hasHumanDossierContributions(researchedPlat), true);
  const archivedRes = syncPlatTierToWatchlist([researchedPlat], {
    appNo: "2026-1659",
    newTier: "3",
    nowIso: "2026-10-27T14:00:00Z",
  });
  assert.equal(archivedRes.action, "archived");
  assert.equal(archivedRes.watchedProperties[0].trackerStatus, "Archived (Tier 3)");

  // Sorting places active Tier 1 -> Tier 2 -> Archived (Tier 3)
  const sorted = sortWatchedProperties([
    archivedRes.watchedProperties[0],
    normalizeWatchProperty({ id: "p2", address: "500 Tier Two St", tier: "2" }),
    normalizeWatchProperty({ id: "p1", address: "900 Tier One St", tier: "1" }),
  ]);
  assert.deepEqual(
    sorted.map((p) => p.id),
    ["p1", "p2", "plat-2026-1659"]
  );
});

test("UT-12 & MOD-3: HcadViewportParcelClient bbox caching, AbortSignal cancellation, maxRecordCount quadtree splitting, Pilot Core deduplication, and age/override enrichment", async () => {
  const q1 = quantizeViewportBbox({ west: -95.401, south: 29.741, east: -95.381, north: 29.759 }, 0.004);
  const q2 = quantizeViewportBbox({ west: -95.4005, south: 29.7412, east: -95.3812, north: 29.7588 }, 0.004);
  assert.equal(q1.key, q2.key, "Minor viewport jitter within quantized grid must share cache key");

  let fetchCalls = 0;
  let abortedCount = 0;
  const polyGeom = {
    type: "Polygon",
    coordinates: [
      [
        [-95.39, 29.75],
        [-95.389, 29.75],
        [-95.389, 29.751],
        [-95.39, 29.751],
        [-95.39, 29.75],
      ],
    ],
  };

  // Stub fetch: first test cancellation + caching, then test maxRecordCount quadtree splitting
  const slowThenFastFetch = (url, { signal } = {}) =>
    new Promise((resolve, reject) => {
      fetchCalls += 1;
      const callNo = fetchCalls;
      if (signal) {
        signal.addEventListener("abort", () => {
          abortedCount += 1;
          const err = new Error("The operation was aborted");
          err.name = "AbortError";
          reject(err);
        });
      }
      setTimeout(() => {
        resolve({
          ok: true,
          status: 200,
          json: async () => ({
            type: "FeatureCollection",
            features: [
              {
                type: "Feature",
                properties: {
                  HCAD_NUM: `041017002000${callNo}`,
                  address: "100 OUTER CORE ST",
                  state_class: "A1",
                },
                geometry: polyGeom,
              },
            ],
          }),
        });
      }, callNo === 1 ? 40 : 5);
    });

  const client = new HcadViewportParcelClient({
    fetchImpl: slowThenFastFetch,
    minFetchZoom: 15.0,
    maxRecordCount: 10,
  });

  // Below minFetchZoom -> zero network calls
  const lowZoom = await client.fetchForViewport({
    west: -95.4,
    south: 29.74,
    east: -95.38,
    north: 29.76,
    zoom: 13.5,
  });
  assert.equal(lowZoom.updated, false);
  assert.equal(fetchCalls, 0);

  // Start slow request A, immediately supersede with request B -> A aborted, B succeeds
  const pA = client.fetchForViewport({
    west: -95.40,
    south: 29.74,
    east: -95.38,
    north: 29.76,
    zoom: 15.5,
  });
  const pB = client.fetchForViewport({
    west: -95.50,
    south: 29.74,
    east: -95.48,
    north: 29.76,
    zoom: 15.5,
  });
  const [resA, resB] = await Promise.all([pA, pB]);
  assert.equal(resA.aborted, true);
  assert.equal(abortedCount, 1);
  assert.equal(resB.updated, true);

  // Repeat identical quantized viewport -> served from LRU cache without network call
  const callsBeforeCache = fetchCalls;
  const resCached = await client.fetchForViewport({
    west: -95.50,
    south: 29.74,
    east: -95.48,
    north: 29.76,
    zoom: 15.6,
  });
  assert.equal(resCached.fromCache, true);
  assert.equal(fetchCalls, callsBeforeCache);

  // Verify maxRecordCount truncation triggers 4-quadrant recursive split and deduplicates boundary parcels
  let splitCalls = 0;
  const splittingClient = new HcadViewportParcelClient({
    maxRecordCount: 2,
    maxSplitDepth: 1,
    fetchImpl: async () => {
      splitCalls += 1;
      const thisCall = splitCalls;
      if (thisCall === 1) {
        return {
          ok: true,
          json: async () => ({
            exceededTransferLimit: true,
            features: [
              { type: "Feature", properties: { HCAD_NUM: "0410170020101" }, geometry: polyGeom },
              { type: "Feature", properties: { HCAD_NUM: "0410170020102" }, geometry: polyGeom },
            ],
          }),
        };
      }
      return {
        ok: true,
        json: async () => ({
          exceededTransferLimit: false,
          features: [
            {
              type: "Feature",
              properties: { HCAD_NUM: `041017002010${thisCall}` },
              geometry: polyGeom,
            },
            // Shared boundary parcel returned by multiple quadrants -> must be deduplicated
            {
              type: "Feature",
              properties: { HCAD_NUM: "0410170020999" },
              geometry: polyGeom,
            },
          ],
        }),
      };
    },
  });
  const splitRes = await splittingClient.fetchForViewport({
    west: -95.44,
    south: 29.74,
    east: -95.40,
    north: 29.78,
    zoom: 15.5,
  });
  assert.equal(splitCalls, 5, "Parent truncated envelope + 4 child quadrants = 5 calls");
  assert.equal(splitRes.features.length, 5, "4 unique quadrant parcels + 1 deduplicated boundary parcel");

  // Verify Pilot Core deduplication, multi-building min(year_built >= 1836), wrapped overrides, 1840 decade normalization, and vacant year_built == 0
  const buildingYears = collectMinBuildingYearsByHcad([
    { properties: { hcad_num: "0410170020200", year_built: 0 } },
    { properties: { hcad_num: "0410170020200", year_built: 1926 } },
    { properties: { hcad_num: "0410170020200", year_built: 1998 } },
  ]);
  assert.equal(buildingYears.get("0410170020200").year_built, 1926);

  const unified = buildUnifiedParcelCollection({
    pilotFeatures: [
      {
        type: "Feature",
        properties: { hcad_num: "0152300000001", address: "2000 SMITH ST", year_built: 1894 },
        geometry: polyGeom,
      },
    ],
    dynamicRawFeatures: [
      // Duplicate of Pilot Core parcel -> must be excluded from dynamic additions
      {
        type: "Feature",
        properties: { HCAD_NUM: "152300000001", address: "2000 SMITH ST" },
        geometry: polyGeom,
      },
      // Multi-building outer-core parcel -> 1926
      {
        type: "Feature",
        properties: { HCAD_NUM: "0410170020200", address: "2200 OUTER ST", state_class: "A1" },
        geometry: polyGeom,
      },
      // Early Republic-era parcel via wrapped curatedOverrides -> 1847 -> decade 1840
      {
        type: "Feature",
        properties: { HCAD_NUM: "0410170020201", address: "300 REPUBLIC AVE", state_class: "F1" },
        geometry: polyGeom,
      },
      // Vacant outer-core lot -> year_built 0, decade 0, Vacant / Unimproved
      {
        type: "Feature",
        properties: { HCAD_NUM: "0410170020202", address: "0 VACANT LOT", state_class: "C1" },
        geometry: polyGeom,
      },
    ],
    buildingYearsByHcad: buildingYears,
    curatedOverrides: {
      overrides: {
        "0410170020201": { year_built: 1847, landmark_name: "Early Republic Structure" },
      },
    },
  });

  assert.equal(unified.features.length, 4, "Pilot Core parcel must appear once alongside 3 outer-core parcels");
  const byHcad = new Map(unified.features.map((f) => [normalizeHcad13(f.properties.hcad_num), f.properties]));
  assert.equal(byHcad.get("0410170020200").year_built, 1926);
  assert.equal(byHcad.get("0410170020200").decade, 1920);
  assert.equal(byHcad.get("0410170020201").year_built, 1847);
  assert.equal(byHcad.get("0410170020201").decade, 1840);
  assert.equal(byHcad.get("0410170020201").landmark_name, "Early Republic Structure");
  assert.equal(byHcad.get("0410170020202").year_built, 0);
  assert.equal(byHcad.get("0410170020202").use_category, "Vacant / Unimproved");
});

test("MOD-1, SCH-1, SCH-3, SCH-4, SCH-5: Modularity, multi-cycle schema, zero magic checklist indices, and confirmed-only Watchlist invariants", () => {
  // MOD-1: plat-monitor.html imports pure ES modules via <script type="module">
  const htmlSrc = fs.readFileSync(path.join(REPO_ROOT, "app", "plat-monitor.html"), "utf8");
  assert.match(htmlSrc, /<script\s+type=["']module["']>/);
  assert.match(htmlSrc, /from\s+["']\.\/js\/platMonitorHelpers\.js\?v=/);
  assert.match(htmlSrc, /from\s+["']\.\/js\/watchlistSyncHelpers\.js\?v=/);

  // SCH-3: Zero positional checklist[0..14] magic array indexing in plat-monitor.html or fetch_plat_and_watchlist.py
  const pySrc = fs.readFileSync(
    path.join(REPO_ROOT, "pipeline", "atlas_pipeline", "fetch_plat_and_watchlist.py"),
    "utf8"
  );
  assert.equal(/checklist\[\s*\d+\s*\]/.test(htmlSrc), false, "plat-monitor.html must use row.id, not checklist[N]");
  assert.equal(/checklist\[\s*\d+\s*\]/.test(pySrc), false, "fetch_plat_and_watchlist.py must use row['id'], not checklist[N]");

  // SCH-1: Multi-cycle plat_agenda_monitor.json preserves backward-compatible top-level keys + cycles map
  const platData = JSON.parse(
    fs.readFileSync(path.join(REPO_ROOT, "app", "public", "data", "plat_agenda_monitor.json"), "utf8")
  );
  assert.ok(Array.isArray(platData.items) && platData.items.length > 0);
  assert.equal(typeof platData.meetingDate, "string");
  assert.equal(typeof platData.reviews, "object");
  assert.ok(platData.cycles && typeof platData.cycles === "object");
  for (const expectedCycle of ["2026-10-01", "2026-10-15", "2026-10-29"]) {
    assert.ok(
      platData.cycles[expectedCycle],
      `Expected cycle ${expectedCycle} in plat_agenda_monitor.json`
    );
  }

  // SCH-4 & SCH-5: property_watchlist.json contains ONLY confirmed watched properties and intact Kirby Mansion snowball terms
  const wlData = JSON.parse(
    fs.readFileSync(path.join(REPO_ROOT, "app", "public", "data", "property_watchlist.json"), "utf8")
  );
  assert.ok(Array.isArray(wlData.watchedProperties) && wlData.watchedProperties.length >= 1);
  assert.ok(wlData.lastAutoSweepAt, "property_watchlist.json must record top-level lastAutoSweepAt");

  for (const prop of wlData.watchedProperties) {
    assert.equal(
      prop.tierConfirmed,
      true,
      `Every property in property_watchlist.json must have tierConfirmed === true (failed on ${prop.id})`
    );
    assert.equal(Array.isArray(prop.checklist) && prop.checklist.length, 15);
  }

  const kirby = wlData.watchedProperties.find((p) => p.id === "prop-kirby-mansion");
  assert.ok(kirby, "prop-kirby-mansion must be present in property_watchlist.json");
  assert.ok(
    kirby.searchTerms?.entities?.includes("Smith St C Store LLC") &&
      kirby.searchTerms?.entities?.includes("OB Texas Properties LLC"),
    "Kirby Mansion must retain its seeded snowball entities in property_watchlist.json"
  );
  assert.ok(
    kirby.searchTerms?.dbas?.includes("Brake Time"),
    "Kirby Mansion must retain its seeded snowball DBAs in property_watchlist.json"
  );
  assert.ok(
    kirby.searchTerms?.altAddresses?.includes("530 Pierce St."),
    "Kirby Mansion must retain its perimeter altAddresses in property_watchlist.json"
  );
});
