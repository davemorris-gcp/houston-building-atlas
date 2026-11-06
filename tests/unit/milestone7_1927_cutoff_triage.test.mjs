import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO_ROOT = path.resolve(__dirname, "../..");
const DATA_DIR = path.join(REPO_ROOT, "app", "public", "data");
const ACTIVE_VER = String(
  JSON.parse(fs.readFileSync(path.join(DATA_DIR, "pmtiles_manifest.json"), "utf8")).cache_bust || ""
).trim();

const {
  AUDIT_CATEGORY_LABELS,
  AUDIT_UNDESIGNATED_DISTRICT,
  AUDIT_VIRTUAL_PRESETS,
  STREET_WALK_DIR_TOKENS,
  STREET_WALK_TYPE_SUFFIXES,
  STREET_WALK_UNIT_CUT_TOKENS,
  STREET_WALK_UNNUMBERED_SENTINEL,
  StaffWorkbenchController,
  UNDESIGNATED_DISTRICT_SENTINELS,
  VALID_AUDIT_SORT_MODES,
  _setAuditStorageOverrideForTests,
  filterAuditQueueItems,
  hydrateAuditQueuePayload,
  is1927CutoffTriageItem,
  isFindingResolvedByOverrides,
  isValidAuditCategoryOrPreset,
  isValidHistoricYear,
  matchesAuditCategoryFilter,
  normalizeStreetStemForAliases,
  parseAuditStreetWalkKey,
  parseHistoricYearOrNull,
  resolveAuditItemStatus,
  saveAuditDecision,
  selectVisibleAuditQueueItems,
  sortAuditQueueItems,
} = await import(`../../app/js/staffWorkbench.js?v=${ACTIVE_VER}`);

const filterStoreMod = await import(`../../app/js/filterStore.js?v=${ACTIVE_VER}`);
const {
  DEFAULT_FILTER_STATE,
  createFilterStore,
  normalizeAuditSort,
  parseHashToState,
  serializeStateToHash,
} = filterStoreMod;
const {
  assignSiblingOverrideProps,
  computePolygonBboxArea,
  computePolygonRingCentroid,
  createParcelFootprintRegistry,
  explodePolygonParts,
  hasMatchingFootprintCentroid,
  isBareAttributeParcelOverride,
  isPointInPolygon,
  isSingleShardReplaceOverride,
} = await import(`../../app/js/curatedEdits.js?v=${ACTIVE_VER}`);
const { AtlasMapController } = await import(`../../app/js/mapController.js?v=${ACTIVE_VER}`);

const { DEFAULT_POLICY_IMPACT_BRIEF } = await import(
  `../../app/js/platMonitorHelpers.js?v=${ACTIVE_VER}`
);

function makeMemoryStorage() {
  const store = new Map();
  return {
    getItem(k) {
      return store.has(k) ? store.get(k) : null;
    },
    setItem(k, v) {
      store.set(String(k), String(v));
    },
    removeItem(k) {
      store.delete(String(k));
    },
    clear() {
      store.clear();
    },
  };
}

function createMinimalAuditDom() {
  const elements = new Map();
  const makeEl = (id) => {
    const classes = new Set(String(id).endsWith("-modal") ? ["hidden"] : []);
    const attrs = new Map();
    const el = {
      id,
      value: "",
      innerHTML: "",
      textContent: "",
      style: { width: "" },
      options: [],
      dataset: {},
      classList: {
        add: (...names) => names.forEach((n) => classes.add(n)),
        remove: (...names) => names.forEach((n) => classes.delete(n)),
        contains: (n) => classes.has(n),
        toggle: (n, force) => {
          const next = force === undefined ? !classes.has(n) : Boolean(force);
          if (next) classes.add(n);
          else classes.delete(n);
          return next;
        },
      },
      setAttribute(k, v) {
        attrs.set(String(k), String(v));
      },
      getAttribute(k) {
        return attrs.has(String(k)) ? attrs.get(String(k)) : null;
      },
      removeAttribute(k) {
        attrs.delete(String(k));
      },
      addEventListener() {},
      removeEventListener() {},
      insertAdjacentHTML(_pos, html) {
        this.innerHTML += html;
      },
      querySelectorAll() {
        return [];
      },
      querySelector() {
        return null;
      },
      focus() {},
    };
    return el;
  };
  const knownIds = [
    "historian-audit-modal",
    "audit-claim-card-host",
    "audit-archival-pane-host",
    "audit-nav-counter",
    "audit-progress-text",
    "audit-progress-fill",
    "audit-filter-category",
    "audit-filter-district",
    "audit-filter-status",
    "audit-sort-order",
    "audit-filter-search",
    "audit-input-note",
    "audit-input-year",
    "audit-input-address",
    "audit-select-source-type",
    "audit-action-feedback",
  ];
  for (const id of knownIds) {
    elements.set(id, makeEl(id));
  }
  return {
    elements,
    documentStub: {
      getElementById(id) {
        if (!elements.has(id)) {
          elements.set(id, makeEl(id));
        }
        return elements.get(id);
      },
      querySelectorAll() {
        return [];
      },
      addEventListener() {},
      removeEventListener() {},
    },
  };
}

test("SA-1 & UT-4: bare Node import isolation, parseHistoricYearOrNull, and is1927CutoffTriageItem null/0 coercion guards", () => {
  assert.equal(typeof window, "undefined");
  assert.equal(typeof document, "undefined");
  assert.equal(
    AUDIT_CATEGORY_LABELS["1930_PLACEHOLDER_PRE1927_PLAT"],
    "1930 Placeholder in Pre-1925 Plat / HD"
  );
  assert.equal(
    AUDIT_VIRTUAL_PRESETS["1927_CUTOFF_TRIAGE"],
    "1927 (100-Yr) Cutoff Triage"
  );
  assert.deepEqual(VALID_AUDIT_SORT_MODES, ["priority", "street_block"]);

  // Year boundary & type-coercion checks (`DF-4`)
  assert.equal(parseHistoricYearOrNull(null), null);
  assert.equal(parseHistoricYearOrNull(undefined), null);
  assert.equal(parseHistoricYearOrNull(""), null);
  assert.equal(parseHistoricYearOrNull(0), null);
  assert.equal(parseHistoricYearOrNull("0"), null);
  assert.equal(parseHistoricYearOrNull(false), null);
  assert.equal(parseHistoricYearOrNull(true), null);
  assert.equal(parseHistoricYearOrNull(1835), null);
  assert.equal(parseHistoricYearOrNull(1836), 1836);
  assert.equal(parseHistoricYearOrNull("1927"), 1927);
  assert.equal(parseHistoricYearOrNull(2035), 2035);
  assert.equal(parseHistoricYearOrNull(2036), null);
  assert.equal(parseHistoricYearOrNull(1920.5), null);

  assert.equal(isValidHistoricYear(null), false);
  assert.equal(isValidHistoricYear(0), false);
  assert.equal(isValidHistoricYear(1835), false);
  assert.equal(isValidHistoricYear(1836), true);
  assert.equal(isValidHistoricYear(1927), true);

  assert.equal(isValidAuditCategoryOrPreset("1927_CUTOFF_TRIAGE"), true);
  assert.equal(isValidAuditCategoryOrPreset("1930_PLACEHOLDER_PRE1927_PLAT"), true);
  assert.equal(isValidAuditCategoryOrPreset("all"), false);
  assert.equal(isValidAuditCategoryOrPreset("NOT_A_CATEGORY"), false);

  // Category 1: 1930_PLACEHOLDER_PRE1927_PLAT -> always included in 1927 cutoff triage
  assert.equal(
    is1927CutoffTriageItem({
      category: "1930_PLACEHOLDER_PRE1927_PLAT",
      atlas_year: 1930,
      suggested_year: null,
    }),
    true
  );

  // Category 2: CITY_DIRECTORY_EARLIER_THAN_HCAD -> sy in 1836..1927 AND (ay >= 1928 or unknown)
  assert.equal(
    is1927CutoffTriageItem({
      category: "CITY_DIRECTORY_EARLIER_THAN_HCAD",
      suggested_year: 1926,
      atlas_year: 1928,
    }),
    true
  );
  assert.equal(
    is1927CutoffTriageItem({
      category: "CITY_DIRECTORY_EARLIER_THAN_HCAD",
      suggested_year: 1927,
      atlas_year: 0,
    }),
    true
  );
  // Pre-1927 directory vs ay == 1927 -> straddles 2026 100_plus (<= 1926) & matches Sheet Bucket A
  assert.equal(
    is1927CutoffTriageItem({
      category: "CITY_DIRECTORY_EARLIER_THAN_HCAD",
      suggested_year: 1910,
      atlas_year: 1927,
    }),
    true
  );
  // Both already <= 1926 (or both == 1927) -> already protected, excluded from cutoff triage
  assert.equal(
    is1927CutoffTriageItem({
      category: "CITY_DIRECTORY_EARLIER_THAN_HCAD",
      suggested_year: 1910,
      atlas_year: 1926,
    }),
    false
  );
  assert.equal(
    is1927CutoffTriageItem({
      category: "CITY_DIRECTORY_EARLIER_THAN_HCAD",
      suggested_year: 1927,
      atlas_year: 1927,
    }),
    false
  );
  // Null or 0 suggested_year MUST NOT coerce via (null <= 1927) -> true (`DF-4`)
  assert.equal(
    is1927CutoffTriageItem({
      category: "CITY_DIRECTORY_EARLIER_THAN_HCAD",
      suggested_year: null,
      atlas_year: 1940,
    }),
    false
  );
  assert.equal(
    is1927CutoffTriageItem({
      category: "CITY_DIRECTORY_EARLIER_THAN_HCAD",
      suggested_year: 0,
      atlas_year: 1940,
    }),
    false
  );

  // Category 3: CITY_DIRECTORY_ABSENT_THROUGH_1926 -> ay in 1836..1927
  assert.equal(
    is1927CutoffTriageItem({
      category: "CITY_DIRECTORY_ABSENT_THROUGH_1926",
      atlas_year: 1920,
      suggested_year: null,
    }),
    true
  );
  assert.equal(
    is1927CutoffTriageItem({
      category: "CITY_DIRECTORY_ABSENT_THROUGH_1926",
      atlas_year: 1928,
      suggested_year: null,
    }),
    false
  );
  assert.equal(
    is1927CutoffTriageItem({
      category: "CITY_DIRECTORY_ABSENT_THROUGH_1926",
      atlas_year: 0,
      suggested_year: null,
    }),
    false
  );

  // Category 4: LANDMARK_VS_ATLAS_YEAR_MISMATCH -> straddles 1927 (or pre-1927 landmark vs unknown ay)
  assert.equal(
    is1927CutoffTriageItem({
      category: "LANDMARK_VS_ATLAS_YEAR_MISMATCH",
      suggested_year: 1925,
      atlas_year: 1935,
    }),
    true
  );
  assert.equal(
    is1927CutoffTriageItem({
      category: "LANDMARK_VS_ATLAS_YEAR_MISMATCH",
      suggested_year: 1932,
      atlas_year: 1920,
    }),
    true
  );
  // Pre-1927 landmark with unknown/0 Atlas year -> MUST be included (`DF-4`)
  assert.equal(
    is1927CutoffTriageItem({
      category: "LANDMARK_VS_ATLAS_YEAR_MISMATCH",
      suggested_year: 1912,
      atlas_year: 0,
    }),
    true
  );
  // Post-1927 landmark missing suggested_year -> MUST NOT false-positive (`DF-4`)
  assert.equal(
    is1927CutoffTriageItem({
      category: "LANDMARK_VS_ATLAS_YEAR_MISMATCH",
      suggested_year: null,
      atlas_year: 1948,
    }),
    false
  );
  // Both sides <= 1927 or both >= 1928 -> false
  assert.equal(
    is1927CutoffTriageItem({
      category: "LANDMARK_VS_ATLAS_YEAR_MISMATCH",
      suggested_year: 1912,
      atlas_year: 1925,
    }),
    false
  );
  assert.equal(
    is1927CutoffTriageItem({
      category: "LANDMARK_VS_ATLAS_YEAR_MISMATCH",
      suggested_year: 1930,
      atlas_year: 1940,
    }),
    false
  );

  // Non-cutoff categories -> false
  assert.equal(
    is1927CutoffTriageItem({
      category: "ADDRESS_STREET_MISMATCH",
      suggested_year: 1910,
      atlas_year: 1940,
    }),
    false
  );
  assert.equal(
    is1927CutoffTriageItem({
      category: "SUSPECTED_UNMARKED_LANDMARK_DEMOLITION",
      suggested_year: 1905,
      atlas_year: 1940,
    }),
    false
  );
  assert.equal(
    is1927CutoffTriageItem({
      category: "ARCHITECT_CAREER_WINDOW_CHECK",
      suggested_year: 1918,
      atlas_year: 1940,
    }),
    false
  );
});

test("UT-4: isFindingResolvedByOverrides and filterAuditQueueItems on 1930_PLACEHOLDER_PRE1927_PLAT and 1927_CUTOFF_TRIAGE", () => {
  const platFinding = {
    fid: "plat_1930:0111222333444",
    category: "1930_PLACEHOLDER_PRE1927_PLAT",
    hcad_num: "0111222333444",
    atlas_year: 1930,
    suggested_year: null,
    district: "Houston Heights Historic District East",
  };
  assert.equal(isFindingResolvedByOverrides(platFinding, {}), false);
  // Same year 1930 does NOT resolve
  assert.equal(
    isFindingResolvedByOverrides(platFinding, {
      "0111222333444": { year_built: 1930 },
    }),
    false
  );
  // Updated pre-1927 year resolves
  assert.equal(
    isFindingResolvedByOverrides(platFinding, {
      "0111222333444": { year_built: 1922 },
    }),
    true
  );
  // Confirmed post-1927 year resolves
  assert.equal(
    isFindingResolvedByOverrides(platFinding, {
      "0111222333444": { year_built: 1938 },
    }),
    true
  );
  // Demolished / vacant flag resolves
  assert.equal(
    isFindingResolvedByOverrides(platFinding, {
      "0111222333444": { is_demolished_vacant_lot: true },
    }),
    true
  );

  const sampleQueue = [
    platFinding,
    {
      fid: "cdir_early:0111222333445",
      category: "CITY_DIRECTORY_EARLIER_THAN_HCAD",
      hcad_num: "0111222333445",
      atlas_year: 1935,
      suggested_year: 1915,
      district: AUDIT_UNDESIGNATED_DISTRICT,
    },
    {
      fid: "cdir_early:0111222333446",
      category: "CITY_DIRECTORY_EARLIER_THAN_HCAD",
      hcad_num: "0111222333446",
      atlas_year: 1922,
      suggested_year: 1908,
      district: AUDIT_UNDESIGNATED_DISTRICT,
    },
    {
      fid: "addr_mm:0111222333447",
      category: "ADDRESS_STREET_MISMATCH",
      hcad_num: "0111222333447",
      atlas_year: 1940,
      suggested_year: 1910,
      district: AUDIT_UNDESIGNATED_DISTRICT,
    },
  ];
  const filteredPreset = filterAuditQueueItems(sampleQueue, {
    category: "1927_CUTOFF_TRIAGE",
  });
  assert.deepEqual(
    filteredPreset.map((x) => x.fid),
    ["plat_1930:0111222333444", "cdir_early:0111222333445"]
  );
  assert.equal(matchesAuditCategoryFilter(platFinding, "1927_CUTOFF_TRIAGE"), true);
  assert.equal(matchesAuditCategoryFilter(sampleQueue[2], "1927_CUTOFF_TRIAGE"), false);
});

test("UT-5: parseAuditStreetWalkKey and non-mutating sortAuditQueueItems across Houston edge-case addresses", () => {
  const kFrac = parseAuditStreetWalkKey(
    "1412 1/2 Columbia St",
    "Houston Heights Historic District South",
    "fid_a"
  );
  assert.deepEqual(kFrac.tuple, [
    "HOUSTON HEIGHTS HISTORIC DISTRICT SOUTH",
    "COLUMBIA",
    1,
    1412,
    "fid_a",
  ]);

  const kHyphen = parseAuditStreetWalkKey(
    "1216-1218 Wilson St",
    "Freedmen's Town Historic District",
    "fid_b"
  );
  assert.deepEqual(kHyphen.tuple, [
    "FREEDMEN'S TOWN HISTORIC DISTRICT",
    "WILSON",
    1,
    1216,
    "fid_b",
  ]);

  const kSuffix = parseAuditStreetWalkKey("1413A Heights Blvd", "Outside Historic District", "fid_c");
  assert.deepEqual(kSuffix.tuple, ["ZZZ_UNDESIGNATED", "HEIGHTS", 0, 1413, "fid_c"]);

  const kNumDir = parseAuditStreetWalkKey("520 W 19th St, Houston, TX 77008", "", "fid_d");
  assert.deepEqual(kNumDir.tuple, ["ZZZ_UNDESIGNATED", "19TH", 1, 520, "fid_d"]);

  const kNumPlain = parseAuditStreetWalkKey("711 75th St", "Undesignated / Outside HD", "fid_e");
  assert.deepEqual(kNumPlain.tuple, ["ZZZ_UNDESIGNATED", "75TH", 0, 711, "fid_e"]);

  const kCorner = parseAuditStreetWalkKey(
    "1001 McKinney St / 921 Main St",
    "Main Street Market Square Historic District",
    "fid_f"
  );
  assert.deepEqual(kCorner.tuple, [
    "MAIN STREET MARKET SQUARE HISTORIC DISTRICT",
    "MCKINNEY",
    0,
    1001,
    "fid_f",
  ]);

  const kUnit = parseAuditStreetWalkKey("815 NW Norhill Blvd Rear Apt B", "Norhill Historic District", "fid_g");
  assert.deepEqual(kUnit.tuple, ["NORHILL HISTORIC DISTRICT", "NORHILL", 0, 815, "fid_g"]);

  const kDirNamed = parseAuditStreetWalkKey("100 North St", "Germantown Historic District", "fid_h");
  assert.deepEqual(kDirNamed.tuple, ["GERMANTOWN HISTORIC DISTRICT", "NORTH", 1, 100, "fid_h"]);

  const kZero = parseAuditStreetWalkKey("0 Bellfort Ave", "", "fid_z0");
  assert.deepEqual(kZero.tuple, [
    "ZZZ_UNDESIGNATED",
    "BELLFORT",
    2,
    STREET_WALK_UNNUMBERED_SENTINEL,
    "fid_z0",
  ]);

  const kUnnum = parseAuditStreetWalkKey("Main St", "", "fid_z1");
  assert.deepEqual(kUnnum.tuple, [
    "ZZZ_UNDESIGNATED",
    "MAIN",
    2,
    STREET_WALK_UNNUMBERED_SENTINEL,
    "fid_z1",
  ]);

  // Verify non-mutating sort behavior and Odd (0) -> Even (1) -> Unnumbered (2) blockface progression
  const unsorted = [
    {
      fid: "item_unnum",
      index: 0,
      priority: 99,
      district: "Norhill Historic District",
      address: "0 Norhill Blvd",
    },
    {
      fid: "item_even_820",
      index: 1,
      priority: 70,
      district: "Norhill Historic District",
      address: "820 Norhill Blvd",
    },
    {
      fid: "item_odd_815",
      index: 2,
      priority: 65,
      district: "Norhill Historic District",
      address: "815 Norhill Blvd",
    },
    {
      fid: "item_even_812",
      index: 3,
      priority: 95,
      district: "Norhill Historic District",
      address: "812 Norhill Blvd",
    },
    {
      fid: "item_odd_803",
      index: 4,
      priority: 60,
      district: "Norhill Historic District",
      address: "803 Norhill Blvd",
    },
  ];
  const snapshotBefore = unsorted.map((x) => x.fid);
  const byStreet = sortAuditQueueItems(unsorted, "street_block");
  assert.deepEqual(
    unsorted.map((x) => x.fid),
    snapshotBefore,
    "sortAuditQueueItems must not mutate input array"
  );
  assert.deepEqual(
    byStreet.map((x) => x.fid),
    ["item_odd_803", "item_odd_815", "item_even_812", "item_even_820", "item_unnum"]
  );

  const byPriority = sortAuditQueueItems(byStreet, "priority");
  assert.deepEqual(
    byPriority.map((x) => x.fid),
    ["item_unnum", "item_even_812", "item_even_820", "item_odd_815", "item_odd_803"]
  );
});

test("UT-6 & DF-3: URL hash round-trip, controller auto-advance sort preservation, and Back/close category reset", async () => {
  assert.equal(DEFAULT_FILTER_STATE.activeAuditSort, "priority");
  assert.equal(normalizeAuditSort("street_block"), "street_block");
  assert.equal(normalizeAuditSort("street"), "street_block");
  assert.equal(normalizeAuditSort("walk"), "street_block");
  assert.equal(normalizeAuditSort("priority"), "priority");
  assert.equal(normalizeAuditSort("BOGUS_SORT"), "priority");
  assert.equal(normalizeAuditSort(""), "priority");

  // Round-trip #audit=1&auditCat=1927_CUTOFF_TRIAGE&auditSort=street_block
  const parsedPreset = parseHashToState(
    "#audit=1&auditCat=1927_CUTOFF_TRIAGE&auditSort=street_block"
  );
  assert.equal(parsedPreset.activeAudit, true);
  assert.equal(parsedPreset.activeAuditCategory, "1927_CUTOFF_TRIAGE");
  assert.equal(parsedPreset.activeAuditSort, "street_block");

  const hashPreset = serializeStateToHash(
    {
      ...DEFAULT_FILTER_STATE,
      activeAudit: true,
      activeAuditCategory: "1927_CUTOFF_TRIAGE",
      activeAuditSort: "street_block",
    },
    null,
    { includeViewport: false }
  );
  assert.ok(hashPreset.includes("audit=1"));
  assert.ok(hashPreset.includes("auditCat=1927_CUTOFF_TRIAGE"));
  assert.ok(hashPreset.includes("auditSort=street_block"));

  // Item-specific fid round-trip
  const fidHash = serializeStateToHash(
    {
      ...DEFAULT_FILTER_STATE,
      activeAudit: "plat_1930:0123456789012",
      activeAuditCategory: "1927_CUTOFF_TRIAGE",
      activeAuditSort: "street_block",
    },
    null,
    { includeViewport: false }
  );
  const parsedFid = parseHashToState(`#${fidHash.replace(/^#/, "")}`);
  assert.equal(parsedFid.activeAudit, "plat_1930:0123456789012");
  assert.equal(parsedFid.activeAuditCategory, "1927_CUTOFF_TRIAGE");
  assert.equal(parsedFid.activeAuditSort, "street_block");

  // Default priority sort omitted from URL hash (`RP-3`)
  const defaultAuditHash = serializeStateToHash(
    {
      ...DEFAULT_FILTER_STATE,
      activeAudit: true,
      activeAuditCategory: "all",
      activeAuditSort: "priority",
    },
    null,
    { includeViewport: false }
  );
  assert.ok(defaultAuditHash.includes("audit=1"));
  assert.ok(!defaultAuditHash.includes("auditCat="));
  assert.ok(!defaultAuditHash.includes("auditSort="));

  // Public share URL omits audit/auditCat/auditSort when activeAudit === false
  const closedHash = serializeStateToHash(
    {
      ...DEFAULT_FILTER_STATE,
      activeAudit: false,
      activeAuditCategory: "1927_CUTOFF_TRIAGE",
      activeAuditSort: "street_block",
    },
    null,
    { includeViewport: false }
  );
  assert.ok(!closedHash.includes("audit"));

  // createFilterStore.resetFilters() clears activeAudit, activeAuditCategory, activeAuditSort
  const store = createFilterStore({
    activeAudit: "plat_1930:0123456789012",
    activeAuditCategory: "1927_CUTOFF_TRIAGE",
    activeAuditSort: "street_block",
  });
  store.resetFilters();
  assert.equal(store.getState().activeAudit, false);
  assert.equal(store.getState().activeAuditCategory, "all");
  assert.equal(store.getState().activeAuditSort, "priority");

  // Controller behavioral verification:
  //   1) `openHistorianAuditModal` with `#audit=<fid>&auditCat=1927_CUTOFF_TRIAGE&auditSort=street_block` retains virtual preset
  //   2) `_advanceToNextPendingAfterTriage` (via `dismissCurrentAuditItem`) keeps `street_block` sort instead of reverting to priority
  //   3) Navigating Back to `#audit=1` (`auditCategory: "all"`) resets `auditFilters.category` to `"all"`
  //   4) `closeHistorianAuditModal()` resets both `category` and `sort` so reopening via sidebar defaults cleanly
  const memStorage = makeMemoryStorage();
  _setAuditStorageOverrideForTests(memStorage);
  const { elements, documentStub } = createMinimalAuditDom();
  const prevDoc = globalThis.document;
  globalThis.document = documentStub;
  try {
    const controllerStore = createFilterStore();
    const wb = new StaffWorkbenchController({
      app: { ensureArchivalCatalogsLoaded: async () => {} },
      mapController: { curatedOverrides: {} },
      filterStore: controllerStore,
    });
    wb.auditQueueDoc = { total_items: 4 };
    wb.auditItems = [
      {
        fid: "cdir_early:0000000000001",
        index: 0,
        priority: 96,
        category: "CITY_DIRECTORY_EARLIER_THAN_HCAD",
        category_label: AUDIT_CATEGORY_LABELS.CITY_DIRECTORY_EARLIER_THAN_HCAD,
        hcad_num: "0000000000001",
        address: "820 Columbia St",
        district: "Houston Heights Historic District South",
        atlas_year: 1935,
        suggested_year: 1912,
        lng: -95.398,
        lat: 29.787,
      },
      {
        fid: "plat_1930:0000000000002",
        index: 1,
        priority: 72,
        category: "1930_PLACEHOLDER_PRE1927_PLAT",
        category_label: AUDIT_CATEGORY_LABELS["1930_PLACEHOLDER_PRE1927_PLAT"],
        hcad_num: "0000000000002",
        address: "803 Columbia St",
        district: "Houston Heights Historic District South",
        atlas_year: 1930,
        suggested_year: null,
        lng: -95.398,
        lat: 29.787,
      },
      {
        fid: "plat_1930:0000000000003",
        index: 2,
        priority: 80,
        category: "1930_PLACEHOLDER_PRE1927_PLAT",
        category_label: AUDIT_CATEGORY_LABELS["1930_PLACEHOLDER_PRE1927_PLAT"],
        hcad_num: "0000000000003",
        address: "815 Columbia St",
        district: "Houston Heights Historic District South",
        atlas_year: 1930,
        suggested_year: null,
        lng: -95.398,
        lat: 29.787,
      },
      {
        fid: "addr_mm:0000000000004",
        index: 3,
        priority: 99,
        category: "ADDRESS_STREET_MISMATCH",
        category_label: AUDIT_CATEGORY_LABELS.ADDRESS_STREET_MISMATCH,
        hcad_num: "0000000000004",
        address: "801 Columbia St",
        district: "Houston Heights Historic District South",
        atlas_year: 1940,
        suggested_year: 1910,
        lng: -95.398,
        lat: 29.787,
      },
    ];

    // Deep link to item #1 inside virtual preset + street_block sort
    await wb.openHistorianAuditModal({
      targetFid: "plat_1930:0000000000002",
      auditCategory: "1927_CUTOFF_TRIAGE",
      auditSort: "street_block",
      updateUrl: true,
    });
    assert.equal(wb.auditFilters.category, "1927_CUTOFF_TRIAGE");
    assert.equal(wb.auditFilters.sort, "street_block");
    // Street-block order: 803 (odd) -> 815 (odd) -> 820 (even); addr_mm excluded!
    assert.deepEqual(
      wb.auditFilteredItems.map((x) => x.address),
      ["803 Columbia St", "815 Columbia St", "820 Columbia St"]
    );
    assert.equal(wb.auditFilteredItems[wb.auditActiveIndex].address, "803 Columbia St");

    // Dismissing 803 Columbia St removes it from pending and advances to 815 Columbia St (next in street_block order, NOT 820 priority 96!)
    wb.dismissCurrentAuditItem();
    assert.equal(wb.auditFilters.sort, "street_block");
    assert.deepEqual(
      wb.auditFilteredItems.map((x) => x.address),
      ["815 Columbia St", "820 Columbia St"]
    );
    assert.equal(wb.auditFilteredItems[wb.auditActiveIndex].address, "815 Columbia St");
    assert.equal(controllerStore.getState().activeAuditCategory, "1927_CUTOFF_TRIAGE");
    assert.equal(controllerStore.getState().activeAuditSort, "street_block");

    // Skipping 815 Columbia St advances to 820 Columbia St while preserving street_block order
    wb.skipCurrentAuditItem();
    assert.equal(wb.auditFilters.sort, "street_block");
    assert.deepEqual(
      wb.auditFilteredItems.map((x) => x.address),
      ["820 Columbia St"]
    );
    assert.equal(wb.auditFilteredItems[wb.auditActiveIndex].address, "820 Columbia St");

    // Simulating browser Back (`popstate`) to `#audit=1` (`auditCategory: "all"`, `auditSort: "priority"`)
    await wb.openHistorianAuditModal({
      targetFid: "",
      auditCategory: "all",
      auditSort: "priority",
      updateUrl: false,
    });
    assert.equal(wb.auditFilters.category, "all");
    assert.equal(wb.auditFilters.sort, "priority");
    assert.equal(elements.get("audit-filter-category").value, "all");
    assert.equal(elements.get("audit-sort-order").value, "priority");
    assert.deepEqual(
      wb.auditFilteredItems.map((x) => x.address),
      ["801 Columbia St", "820 Columbia St"]
    );

    // Re-enter preset, close modal, then re-open default modal (`auditCategory: ""`)
    await wb.openHistorianAuditModal({
      auditCategory: "1927_CUTOFF_TRIAGE",
      auditSort: "street_block",
      updateUrl: true,
    });
    wb.closeHistorianAuditModal({ updateUrl: true });
    assert.equal(wb.auditFilters.category, "all");
    assert.equal(wb.auditFilters.sort, "priority");
    assert.equal(controllerStore.getState().activeAudit, false);
    assert.equal(controllerStore.getState().activeAuditCategory, "all");
    assert.equal(controllerStore.getState().activeAuditSort, "priority");

    await wb.openHistorianAuditModal({
      auditCategory: "",
      updateUrl: true,
    });
    assert.equal(wb.auditFilters.category, "all");
    assert.equal(controllerStore.getState().activeAuditCategory, "all");
  } finally {
    globalThis.document = prevDoc;
    _setAuditStorageOverrideForTests(null);
  }
});

test("IT-2, IT-4 & RP-1: live bundle hydration, DEFAULT_POLICY_IMPACT_BRIEF parity, and 0200670000010 fixture safety", () => {
  const scorecardsRaw = fs.readFileSync(
    path.join(DATA_DIR, "neighborhood_scorecards.json"),
    "utf8"
  );
  assert.ok(!scorecardsRaw.trim().includes("\n"), "neighborhood_scorecards.json must be single-line JSON");
  const scorecardsDoc = JSON.parse(scorecardsRaw);
  assert.deepEqual(
    DEFAULT_POLICY_IMPACT_BRIEF,
    scorecardsDoc.citywide_policy_brief,
    "platMonitorHelpers.js DEFAULT_POLICY_IMPACT_BRIEF must match neighborhood_scorecards.json 1:1"
  );
  assert.equal(
    DEFAULT_POLICY_IMPACT_BRIEF.local_designation_coverage.excludes_heritage_districts,
    true
  );
  const c100 = DEFAULT_POLICY_IMPACT_BRIEF.standing_buildings_by_age.find(
    (r) => r.id === "100_plus"
  );
  assert.ok(c100, "Expected 100_plus bucket in standing_buildings_by_age");
  assert.ok(c100.standing_count >= 13686, `Expected >= 13,686 100+ yr structures, got ${c100.standing_count}`);

  const queuePath = path.join(DATA_DIR, "historical_audit_queue.json");
  const queueStat = fs.statSync(queuePath);
  assert.ok(
    queueStat.size < 995_000,
    `historical_audit_queue.json (${queueStat.size} B) exceeded 995,000 B safety target`
  );
  const queueRaw = fs.readFileSync(queuePath, "utf8");
  assert.ok(!queueRaw.trim().includes("\n"), "historical_audit_queue.json must be single-line JSON");
  const queueDoc = JSON.parse(queueRaw);
  const summaryDoc = JSON.parse(
    fs.readFileSync(path.join(DATA_DIR, "historical_audit_summary.json"), "utf8")
  );
  const statsSummaryDoc = JSON.parse(
    fs.readFileSync(path.join(DATA_DIR, "stats_summary.json"), "utf8")
  );
  const hydrated = hydrateAuditQueuePayload(queueDoc);
  assert.ok(queueDoc.total >= 2600, `Expected >= 2,600 queue items, got ${queueDoc.total}`);
  assert.equal(hydrated.length, queueDoc.total);
  assert.equal(queueDoc.total, summaryDoc.total_queued_contradictions);
  assert.equal(statsSummaryDoc.audit_queue_total, queueDoc.total);

  const plat1930Items = hydrated.filter(
    (it) => it.category === "1930_PLACEHOLDER_PRE1927_PLAT"
  );
  assert.ok(plat1930Items.length >= 200, `Expected >=200 plat_1930 items, got ${plat1930Items.length}`);
  for (const it of plat1930Items) {
    assert.ok(it.fid.startsWith("plat_1930:"));
    assert.equal(it.atlas_year, 1930);
    assert.equal(it.suggested_year, null, "plat_1930 suggested_year must be null so #audit-input-year never pre-fills 0");
    assert.ok(it.displayed_claim.length > 15);
    assert.ok(it.conflicting_evidence.length > 15);
    assert.ok(it.recommended_action.length > 15);
    assert.ok(it.source_type.length > 5);
    assert.ok(it.source_url.startsWith("https://"));
    assert.equal(is1927CutoffTriageItem(it), true);
  }

  // RP-1: Reserved fixture 0200670000010 (2009 Harvard St) remains un-promoted and active in queue
  const overridesRawDoc = JSON.parse(
    fs.readFileSync(path.join(DATA_DIR, "curated_overrides.json"), "utf8")
  );
  const overridesMap = overridesRawDoc.overrides || overridesRawDoc;
  assert.equal(
    Object.prototype.hasOwnProperty.call(overridesMap, "0200670000010"),
    false,
    "0200670000010 must remain un-promoted in curated_overrides.json"
  );
  const harvardFindings = hydrated.filter((it) => it.hcad_num === "0200670000010");
  assert.ok(
    harvardFindings.some((it) => it.category === "CITY_DIRECTORY_EARLIER_THAN_HCAD"),
    "0200670000010 must remain active in CITY_DIRECTORY_EARLIER_THAN_HCAD"
  );
  assert.ok(harvardFindings.length >= 2);
});

test("SOP 4 Case D multi-footprint preservation: centroid tolerance, _applyCuratedOverridesInMemory, and _captureDynamicOverrideGeometries", () => {
  // Dead export cleanup verification
  assert.equal("parseStateFromUrlHash" in filterStoreMod, false);
  assert.equal("serializeStateToUrlHash" in filterStoreMod, false);

  // 1. Centroid stability across rotated closing rings & MVT 4096-extent quantization (< 0.000035 deg)
  const mainHouseGeo = {
    type: "Polygon",
    coordinates: [
      [
        [-95.391, 29.781],
        [-95.3909, 29.781],
        [-95.3909, 29.7811],
        [-95.391, 29.7811],
        [-95.391, 29.781],
      ],
    ],
  };
  const mainHouseQuantizedRotated = {
    type: "Polygon",
    coordinates: [
      [
        [-95.39090001, 29.78100001],
        [-95.39090001, 29.78110001],
        [-95.39100001, 29.78110001],
        [-95.39100001, 29.78100001],
        [-95.39090001, 29.78100001],
      ],
    ],
  };
  // Rear carriage house ~9m north-east (0.00008 deg > 0.000035 deg tolerance)
  const rearCarriageGeo = {
    type: "Polygon",
    coordinates: [
      [
        [-95.39092, 29.78118],
        [-95.39086, 29.78118],
        [-95.39086, 29.78123],
        [-95.39092, 29.78123],
        [-95.39092, 29.78118],
      ],
    ],
  };
  // Detached garage ~16m north (0.00015 deg > 0.000035 deg tolerance)
  const garageGeo = {
    type: "Polygon",
    coordinates: [
      [
        [-95.39102, 29.78125],
        [-95.39096, 29.78125],
        [-95.39096, 29.7813],
        [-95.39102, 29.7813],
        [-95.39102, 29.78125],
      ],
    ],
  };

  const cMain = computePolygonRingCentroid(mainHouseGeo);
  const cMainQ = computePolygonRingCentroid(mainHouseQuantizedRotated);
  const cCarriage = computePolygonRingCentroid(rearCarriageGeo);
  const cGarage = computePolygonRingCentroid(garageGeo);
  assert.ok(cMain && cMainQ && cCarriage && cGarage);
  assert.equal(hasMatchingFootprintCentroid([cMain], cMainQ), true);
  assert.equal(hasMatchingFootprintCentroid([cMain], cCarriage), false);
  assert.equal(hasMatchingFootprintCentroid([cMain, cCarriage], cGarage), false);

  // 2. Controller multi-footprint hydration without mutating curatedOverrides[hcad].geometry
  const hcad = "0050420000003";
  const mc = new AtlasMapController({
    containerId: "map",
    filterStore: createFilterStore(),
    onSelectFeature: () => {},
    onViewportStats: () => {},
  });
  mc.curatedOverrides = {
    [hcad]: {
      hcad_num: hcad,
      address: "1513 RUTHVEN ST",
      year_built: 1915,
      decade: 1910,
      use_category: "Residential",
      own_tier: "local_small",
      contributing: "Outside Historic District",
      verified_by: "Archival City Directory Cross-Check (Double-Vetted)",
    },
  };
  mc.buildingsData = [
    {
      type: "Feature",
      geometry: mainHouseGeo,
      properties: { id: hcad, building_id: hcad, hcad_num: hcad, year_built: 1930 },
    },
    {
      type: "Feature",
      geometry: rearCarriageGeo,
      properties: { id: hcad, building_id: hcad, hcad_num: hcad, year_built: 1930 },
    },
  ];

  mc._applyCuratedOverridesInMemory();
  assert.equal(
    mc.overridesFC.features.length,
    2,
    "_applyCuratedOverridesInMemory must preserve both buildings.geojson footprints on bare SOP 4 Case D override"
  );
  assert.deepEqual(
    mc.overridesFC.features.map((f) => f.properties.id),
    [hcad, `${hcad}#core_fp_1`]
  );
  assert.ok(mc.overridesFC.features.every((f) => f.properties.year_built === 1915));

  // 3. _captureDynamicOverrideGeometries hydrates 3rd sibling from .pmtiles shard, skips quantized duplicates, and stays idempotent
  let setDataCalls = 0;
  mc.shardSourceIds = ["buildings-shard-sw"];
  mc.map = {
    querySourceFeatures() {
      return [
        { geometry: mainHouseQuantizedRotated, properties: { id: hcad, hcad_num: hcad, year_built: 1930 } },
        { geometry: rearCarriageGeo, properties: { id: hcad, hcad_num: hcad, year_built: 1930 } },
        { geometry: garageGeo, properties: { id: hcad, hcad_num: hcad, year_built: 1930 } },
      ];
    },
    getSource(id) {
      if (id === "curated-overrides-src") {
        return {
          setData() {
            setDataCalls++;
          },
        };
      }
      return null;
    },
  };

  mc._captureDynamicOverrideGeometries();
  assert.equal(mc.overridesFC.features.length, 3, "3rd .pmtiles sibling footprint must be dynamically hydrated");
  assert.equal(setDataCalls, 1);
  assert.equal(
    mc.curatedOverrides[hcad].geometry,
    undefined,
    "_captureDynamicOverrideGeometries must never mutate curatedOverrides[hcad].geometry"
  );
  assert.deepEqual(
    mc.overridesFC.features.map((f) => f.properties.id),
    [hcad, `${hcad}#core_fp_1`, `${hcad}#shard_fp_2`]
  );
  assert.ok(mc.overridesFC.features.every((f) => f.properties.own_tier === "local_small"));

  // Repeat call is a no-op (zero duplicate features)
  mc._captureDynamicOverrideGeometries();
  assert.equal(mc.overridesFC.features.length, 3);
  assert.equal(setDataCalls, 1);

  // Subsequent _applyCuratedOverridesInMemory retains cached dynamic shard footprints
  mc._applyCuratedOverridesInMemory();
  assert.equal(mc.overridesFC.features.length, 3);

  // 4. MultiPolygon bare-override refresh deduplication + shared Case D helpers & "Building" unit cutoff
  assert.equal(isBareAttributeParcelOverride(hcad, mc.curatedOverrides[hcad]), true);
  assert.equal(isBareAttributeParcelOverride(`${hcad}#b1`, mc.curatedOverrides[hcad]), false);
  assert.equal(STREET_WALK_UNIT_CUT_TOKENS.has("BUILDING"), true);
  assert.equal(
    parseAuditStreetWalkKey({ address: "1001 Main St Building B", district: "" }).streetStem,
    "MAIN"
  );

  const mpHcad = "0050420000099";
  const multiPolyCore = {
    type: "MultiPolygon",
    coordinates: [mainHouseGeo.coordinates, rearCarriageGeo.coordinates],
  };
  assert.equal(explodePolygonParts(multiPolyCore).length, 2);

  const mcMp = new AtlasMapController({
    containerId: "map",
    filterStore: createFilterStore(),
    onSelectFeature: () => {},
    onViewportStats: () => {},
  });
  mcMp.curatedOverrides = {
    [mpHcad]: {
      hcad_num: mpHcad,
      address: "1521 RUTHVEN ST",
      year_built: 1922,
      decade: 1920,
      use_category: "Residential",
      own_tier: "homestead",
      contributing: "Outside Historic District",
      verified_by: "Archival City Directory Cross-Check (Double-Vetted)",
    },
  };
  mcMp.buildingsData = [
    {
      type: "Feature",
      geometry: multiPolyCore,
      properties: { id: mpHcad, building_id: mpHcad, hcad_num: mpHcad, year_built: 1930 },
    },
  ];
  mcMp._applyCuratedOverridesInMemory();
  assert.equal(mcMp.overridesFC.features.length, 2, "MultiPolygon unpacks into 2 features (#fp_1)");

  mcMp.shardSourceIds = ["buildings-shard-sw"];
  mcMp.map = {
    querySourceFeatures() {
      return [
        { geometry: multiPolyCore, properties: { id: mpHcad, hcad_num: mpHcad, year_built: 1930 } },
        { geometry: garageGeo, properties: { id: mpHcad, hcad_num: mpHcad, year_built: 1930 } },
      ];
    },
    getSource() {
      return { setData() {} };
    },
  };
  mcMp._captureDynamicOverrideGeometries();
  assert.equal(
    mcMp.overridesFC.features.length,
    3,
    "Secondary MultiPolygon part must already be tracked so only the 3rd garage part is appended"
  );
  // Simulate cached MultiPolygon parts re-applying on refresh: secondary part must not duplicate!
  mcMp._dynamicShardGeometriesByHcad.set(mpHcad, [
    { geometry: mainHouseGeo, rawProps: { id: mpHcad, hcad_num: mpHcad, year_built: 1930 } },
    { geometry: rearCarriageGeo, rawProps: { id: mpHcad, hcad_num: mpHcad, year_built: 1930 } },
    { geometry: garageGeo, rawProps: { id: mpHcad, hcad_num: mpHcad, year_built: 1930 } },
  ]);
  mcMp._applyCuratedOverridesInMemory();
  assert.equal(
    mcMp.overridesFC.features.length,
    3,
    "Refresh must keep exactly 3 footprints without duplicating MultiPolygon part #2"
  );

  // 5. Pass-3 regressions: Undesignated COH sentinel, shared alias stem tokens, sibling building_id sync, and countywide replace_parcel_shards single-shard replay
  assert.equal(UNDESIGNATED_DISTRICT_SENTINELS.has("UNDESIGNATED COH"), true);
  assert.deepEqual(
    parseAuditStreetWalkKey({ address: "500 Main St", district: "Undesignated COH", fid: "f_undesig" })
      .tuple,
    ["ZZZ_UNDESIGNATED", "MAIN", 1, 500, "f_undesig"]
  );
  assert.equal(normalizeStreetStemForAliases("610 West Loop South"), "WEST");
  assert.equal(normalizeStreetStemForAliases("1200 Hermann Park Cir"), "HERMANN PARK");

  const sibRes = assignSiblingOverrideProps(
    { id: "0011223344556_carriage", building_id: "0011223344556", hcad_num: "0011223344556" },
    "0011223344556",
    1,
    "shard"
  );
  assert.equal(sibRes.targetKey, "0011223344556#shard_fp_1");
  assert.equal(sibRes.props.id, "0011223344556_carriage");
  assert.equal(sibRes.props.building_id, "0011223344556_carriage");

  const singleReplaceHcad = "0900000000001";
  const singleReplaceOv = {
    hcad_num: singleReplaceHcad,
    address: "9900 Almeda Genoa Rd",
    year_built: 1924,
    decade: 1920,
    replace_parcel_shards: true,
    own_tier: "local_small",
  };
  assert.equal(isSingleShardReplaceOverride(singleReplaceHcad, singleReplaceOv), true);
  assert.equal(isBareAttributeParcelOverride(singleReplaceHcad, singleReplaceOv), false);

  const mcSingleReplace = new AtlasMapController({
    containerId: "map",
    filterStore: createFilterStore(),
    onSelectFeature: () => {},
    onViewportStats: () => {},
  });
  mcSingleReplace.curatedOverrides = { [singleReplaceHcad]: singleReplaceOv };
  mcSingleReplace.buildingsData = [];
  mcSingleReplace._applyCuratedOverridesInMemory();
  assert.equal(mcSingleReplace.overridesFC.features.length, 0);

  mcSingleReplace.shardSourceIds = ["buildings-shard-se"];
  mcSingleReplace.map = {
    querySourceFeatures() {
      return [
        { geometry: mainHouseGeo, properties: { id: singleReplaceHcad, hcad_num: singleReplaceHcad, year_built: 1950 } },
        { geometry: garageGeo, properties: { id: `${singleReplaceHcad}#2`, hcad_num: singleReplaceHcad, year_built: 1950 } },
      ];
    },
    getSource() {
      return { setData() {} };
    },
  };
  mcSingleReplace._captureDynamicOverrideGeometries();
  assert.equal(
    mcSingleReplace.overridesFC.features.length,
    1,
    "replace_parcel_shards without inline geometry must dynamically capture exactly 1 shard polygon"
  );
  // Repeat capture + _applyCuratedOverridesInMemory replay must preserve that 1 polygon without growing cacheList
  mcSingleReplace._captureDynamicOverrideGeometries();
  assert.equal(mcSingleReplace._dynamicShardGeometriesByHcad.get(singleReplaceHcad).length, 1);
  mcSingleReplace._applyCuratedOverridesInMemory();
  assert.equal(
    mcSingleReplace.overridesFC.features.length,
    1,
    "Subsequent _applyCuratedOverridesInMemory must replay the cached single shard polygon"
  );

  // 6. Pass-4 regressions: MVT tile-seam clipped fragment deduplication (`bld_*` ID + point-in-polygon containment),
  //    `"walk"` / `"street"` sort aliases, case-insensitive `matchesAuditCategoryFilter`, and `plat_1930_claim` fallback parity
  assert.equal(
    isPointInPolygon([-95.39095, 29.78105], mainHouseGeo),
    true,
    "Interior point inside mainHouseGeo must return true"
  );
  assert.equal(
    isPointInPolygon([-95.39099, 29.78127], mainHouseGeo),
    false,
    "Disjoint garage point must return false"
  );

  const seamHcad = "0200490000088";
  const fullSeamHouse = {
    type: "Polygon",
    coordinates: [
      [
        [-95.4010, 29.7900],
        [-95.4006, 29.7900],
        [-95.4006, 29.7902],
        [-95.4010, 29.7902],
        [-95.4010, 29.7900],
      ],
    ],
  };
  // East tile-clipped overhang (~8m wide, centroid shifted > 0.00012 deg > 0.000035 deg from fullSeamHouse)
  const eastClippedSliver = {
    type: "Polygon",
    coordinates: [
      [
        [-95.40068, 29.7900],
        [-95.4006, 29.7900],
        [-95.4006, 29.7902],
        [-95.40068, 29.7902],
        [-95.40068, 29.7900],
      ],
    ],
  };
  const westClippedHalf = {
    type: "Polygon",
    coordinates: [
      [
        [-95.4010, 29.7900],
        [-95.40068, 29.7900],
        [-95.40068, 29.7902],
        [-95.4010, 29.7902],
        [-95.4010, 29.7900],
      ],
    ],
  };

  // Case 6a: Countywide MVT tile boundary where two non-overlapping halves share one `bld_*` ID
  const mcSeamBldId = new AtlasMapController({
    containerId: "map",
    filterStore: createFilterStore(),
    onSelectFeature: () => {},
    onViewportStats: () => {},
  });
  mcSeamBldId.curatedOverrides = {
    [seamHcad]: {
      hcad_num: seamHcad,
      address: "1800 HEIGHTS BLVD",
      year_built: 1918,
      decade: 1910,
      own_tier: "homestead",
    },
  };
  mcSeamBldId.buildingsData = [];
  mcSeamBldId._applyCuratedOverridesInMemory();
  mcSeamBldId.shardSourceIds = ["buildings-shard-nw"];
  mcSeamBldId.map = {
    querySourceFeatures() {
      return [
        { geometry: westClippedHalf, properties: { id: "bld_099881", hcad_num: seamHcad, year_built: 1930 } },
        { geometry: eastClippedSliver, properties: { id: "bld_099881", hcad_num: seamHcad, year_built: 1930 } },
        { geometry: garageGeo, properties: { id: "bld_099882", hcad_num: seamHcad, year_built: 1930 } },
      ];
    },
    getSource() {
      return { setData() {} };
    },
  };
  mcSeamBldId._captureDynamicOverrideGeometries();
  assert.equal(
    mcSeamBldId.overridesFC.features.length,
    2,
    "Two MVT tile-clipped halves sharing bld_099881 must deduplicate to 1 main footprint + 1 distinct bld_099882 garage"
  );

  // Case 6b: Bare parcel `id === hcad` where `buildings.geojson` holds full roof and MVT returns a clipped sliver inside it
  const regContainment = createParcelFootprintRegistry();
  regContainment.seedExistingFeature(seamHcad, fullSeamHouse, { id: seamHcad, hcad_num: seamHcad });
  assert.equal(
    regContainment.registerCandidatePart(seamHcad, eastClippedSliver),
    null,
    "MVT clipped sliver inside fullSeamHouse exterior ring must be rejected via point-in-polygon containment"
  );
  assert.notEqual(
    regContainment.registerCandidatePart(seamHcad, garageGeo),
    null,
    "Disjoint secondary garage must register cleanly"
  );

  // Case 6c: `sortAuditQueueItems` / `selectVisibleAuditQueueItems` honor `"walk"` & `"street"` aliases and case-insensitive presets
  const aliasItems = [
    {
      fid: "f_even",
      category: "1930_PLACEHOLDER_PRE1927_PLAT",
      address: "804 Columbia St",
      district: "Houston Heights Historic District South",
      priority: 95,
      index: 0,
    },
    {
      fid: "f_odd",
      category: "1930_PLACEHOLDER_PRE1927_PLAT",
      address: "803 Columbia St",
      district: "Houston Heights Historic District South",
      priority: 60,
      index: 1,
    },
  ];
  assert.deepEqual(
    sortAuditQueueItems(aliasItems, "walk").map((x) => x.fid),
    ["f_odd", "f_even"]
  );
  assert.deepEqual(
    selectVisibleAuditQueueItems(aliasItems, { category: "1927_cutoff_triage", sort: "street" }).map((x) => x.fid),
    ["f_odd", "f_even"]
  );
  assert.equal(matchesAuditCategoryFilter(aliasItems[0], "ALL"), true);
  assert.equal(matchesAuditCategoryFilter(aliasItems[0], "1927_cutoff_triage"), true);
  assert.equal(matchesAuditCategoryFilter(aliasItems[0], "1930_placeholder_pre1927_plat"), true);

  const hydratedFallback = hydrateAuditQueuePayload({
    items: [{ cat: "1930_PLACEHOLDER_PRE1927_PLAT", h: "0111222333444", ay: 1930 }],
  });
  assert.equal(
    hydratedFallback[0].displayed_claim,
    "HCAD Year Built: 1930 (Rounded Decade Placeholder)"
  );
});

test("Review Pass-5 regressions: symmetric 1926/1927 landmark mismatch, MVT sliver bbox ordering & cross-pan upgrade, ingestFeatureParts maxParts, and core single-replace preservation", () => {
  // 1. Symmetric LANDMARK_VS_ATLAS_YEAR_MISMATCH across both 1926 and 1927 boundaries
  assert.equal(
    is1927CutoffTriageItem({
      category: "LANDMARK_VS_ATLAS_YEAR_MISMATCH",
      suggested_year: 1927,
      atlas_year: 1920,
      hcad_year: 1920,
    }),
    true,
    "Landmark 1927 vs HCAD 1920 crosses the 1926 100-year boundary and must be included"
  );
  assert.equal(
    is1927CutoffTriageItem({
      category: "LANDMARK_VS_ATLAS_YEAR_MISMATCH",
      suggested_year: 1925,
      atlas_year: 1927,
      hcad_year: 1927,
    }),
    true
  );
  assert.equal(
    is1927CutoffTriageItem({
      category: "LANDMARK_VS_ATLAS_YEAR_MISMATCH",
      suggested_year: 1915,
      atlas_year: 1922,
      hcad_year: 1922,
    }),
    false
  );
  assert.equal(
    is1927CutoffTriageItem({
      category: "LANDMARK_VS_ATLAS_YEAR_MISMATCH",
      suggested_year: 1935,
      atlas_year: 1945,
      hcad_year: 1945,
    }),
    false
  );

  // 2. No orphaned private alias constants in app/js/staffWorkbench.js
  const wbSrc = fs.readFileSync(path.join(REPO_ROOT, "app", "js", "staffWorkbench.js"), "utf8");
  assert.equal(/\bconst\s+UNIT_TOKENS\b/.test(wbSrc), false);
  assert.equal(/\bconst\s+LEADING_DIRS\b/.test(wbSrc), false);
  assert.equal(/\bconst\s+STREET_TYPE_SUFFIXES\b/.test(wbSrc), false);

  // 3. computePolygonBboxArea + ingestFeatureParts largest-first ordering & maxParts cap
  const smallSliver = {
    type: "Polygon",
    coordinates: [
      [
        [-95.40068, 29.7900],
        [-95.40060, 29.7900],
        [-95.40060, 29.7902],
        [-95.40068, 29.7902],
        [-95.40068, 29.7900],
      ],
    ],
  };
  const fullRoof = {
    type: "Polygon",
    coordinates: [
      [
        [-95.40100, 29.7900],
        [-95.40060, 29.7900],
        [-95.40060, 29.7902],
        [-95.40100, 29.7902],
        [-95.40100, 29.7900],
      ],
    ],
  };
  const disjointShed = {
    type: "Polygon",
    coordinates: [
      [
        [-95.40130, 29.7904],
        [-95.40120, 29.7904],
        [-95.40120, 29.7905],
        [-95.40130, 29.7905],
        [-95.40130, 29.7904],
      ],
    ],
  };
  assert.ok(computePolygonBboxArea(fullRoof) > computePolygonBboxArea(smallSliver) * 4);
  const regMp = createParcelFootprintRegistry();
  const acceptedSingle = regMp.ingestFeatureParts(
    "0200490000111",
    { type: "MultiPolygon", coordinates: [disjointShed.coordinates, fullRoof.coordinates] },
    { id: "bld_mp_1", hcad_num: "0200490000111" },
    { maxParts: 1 }
  );
  assert.equal(acceptedSingle.length, 1);
  assert.deepEqual(acceptedSingle[0].partGeom, fullRoof, "Largest MultiPolygon part must win when maxParts=1");

  // 4. Within-batch sliver-first MVT querySourceFeatures ordering + cross-pan sliver-to-full roof upgrade
  const panHcad = "0200490000222";
  const mcPan = new AtlasMapController({
    containerId: "map",
    filterStore: createFilterStore(),
    onSelectFeature: () => {},
    onViewportStats: () => {},
  });
  mcPan.curatedOverrides = {
    [panHcad]: {
      hcad_num: panHcad,
      address: "1810 HEIGHTS BLVD",
      year_built: 1916,
      decade: 1910,
      own_tier: "homestead",
    },
  };
  mcPan.buildingsData = [];
  mcPan._applyCuratedOverridesInMemory();
  mcPan.shardSourceIds = ["buildings-shard-nw"];

  let panPhase = 1;
  let panSetDataCalls = 0;
  mcPan.map = {
    querySourceFeatures() {
      if (panPhase === 1) {
        // Pan 1: only adjacent MVT tile's clipped edge sliver is loaded
        return [{ geometry: smallSliver, properties: { id: "bld_pan_1", hcad_num: panHcad, year_built: 1930 } }];
      }
      // Pan 2: sliver still returned first, followed by primary tile's unclipped fullRoof
      return [
        { geometry: smallSliver, properties: { id: "bld_pan_1", hcad_num: panHcad, year_built: 1930 } },
        { geometry: fullRoof, properties: { id: "bld_pan_1", hcad_num: panHcad, year_built: 1930 } },
      ];
    },
    getSource(id) {
      return id === "curated-overrides-src" ? { setData() { panSetDataCalls++; } } : null;
    },
  };

  mcPan._captureDynamicOverrideGeometries();
  assert.equal(mcPan.overridesFC.features.length, 1);
  assert.deepEqual(mcPan.overridesFC.features[0].geometry, smallSliver);
  assert.equal(panSetDataCalls, 1);

  panPhase = 2;
  mcPan._captureDynamicOverrideGeometries();
  assert.equal(mcPan.overridesFC.features.length, 1, "Must upgrade existing feature in-place without duplicating");
  assert.deepEqual(
    mcPan.overridesFC.features[0].geometry,
    fullRoof,
    "Cross-pan MVT arrival of unclipped fullRoof must upgrade truncated sliver"
  );
  assert.deepEqual(mcPan._dynamicShardGeometriesByHcad.get(panHcad)[0].geometry, fullRoof);
  assert.equal(panSetDataCalls, 2);

  // 5. _applyCuratedOverridesInMemory preserves core buildings.geojson footprint when replace_parcel_shards=true has cached shard geometry
  const coreReplaceHcad = "0050420000777";
  const mcCoreReplace = new AtlasMapController({
    containerId: "map",
    filterStore: createFilterStore(),
    onSelectFeature: () => {},
    onViewportStats: () => {},
  });
  mcCoreReplace.curatedOverrides = {
    [coreReplaceHcad]: {
      hcad_num: coreReplaceHcad,
      address: "1599 RUTHVEN ST",
      year_built: 1914,
      decade: 1910,
      replace_parcel_shards: true,
      own_tier: "homestead",
    },
  };
  mcCoreReplace.buildingsData = [
    {
      type: "Feature",
      geometry: fullRoof,
      properties: { id: coreReplaceHcad, building_id: coreReplaceHcad, hcad_num: coreReplaceHcad, year_built: 1930 },
    },
  ];
  mcCoreReplace._dynamicShardGeometriesByHcad = new Map([
    [
      coreReplaceHcad,
      [{ geometry: disjointShed, rawProps: { id: `${coreReplaceHcad}#shard`, hcad_num: coreReplaceHcad, year_built: 1930 } }],
    ],
  ]);
  mcCoreReplace._applyCuratedOverridesInMemory();
  assert.equal(mcCoreReplace.overridesFC.features.length, 1);
  assert.deepEqual(
    mcCoreReplace.overridesFC.features[0].geometry,
    fullRoof,
    "Core buildings.geojson footprint must not be overwritten by cached shard replay when replace_parcel_shards=true"
  );
});
