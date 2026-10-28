import test from "node:test";
import assert from "node:assert/strict";

import {
  DEMOLISHED_REPLACED_ISSUE,
  DEMOLISHED_VACANT_CITATION_TAG,
  DEMOLISHED_VACANT_ISSUE,
  applyOverrideToProperties,
  clearAdminSession,
  isDemolishedVacantOverrideInput,
  isEntitySuppressedByOverrides,
  loadCuratedOverrides,
  mergeOverrideRecord,
  normalizeCorrectionSubmission,
  parseCsvToObjects,
  parseOverridesFromSheetRows,
  submitAdminApprovedOverride,
  submitCorrectionSuggestion,
} from "../../app/js/curatedEdits.js";
import {
  collectMinBuildingYearsByHcad,
  enrichParcelFeature,
} from "../../app/js/hcadViewportParcels.js";
import { AtlasMapController } from "../../app/js/mapController.js";
import { HoustonAtlasApp } from "../../app/js/app.js";
import { createFilterStore } from "../../app/js/filterStore.js";

const SAMPLE_BUILDING_POLY = {
  type: "Polygon",
  coordinates: [
    [
      [-95.39, 29.79],
      [-95.3895, 29.79],
      [-95.3895, 29.7905],
      [-95.39, 29.79],
    ],
  ],
};

const SAMPLE_PARCEL_LOT_POLY = {
  type: "Polygon",
  coordinates: [
    [
      [-95.391, 29.789],
      [-95.388, 29.789],
      [-95.388, 29.792],
      [-95.391, 29.789],
    ],
  ],
};

function createInMemoryStorage() {
  const map = new Map();
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: (k) => map.delete(k),
    clear: () => map.clear(),
  };
}

function createMockElement(id, initial = {}) {
  const classes = new Set(initial.classes || []);
  const listeners = new Map();
  return {
    id,
    value: initial.value ?? "",
    textContent: initial.textContent ?? "",
    innerHTML: initial.innerHTML ?? "",
    placeholder: initial.placeholder ?? "",
    required: Boolean(initial.required),
    disabled: Boolean(initial.disabled),
    open: Boolean(initial.open),
    href: initial.href ?? "",
    classList: {
      add: (...names) => names.forEach((n) => classes.add(n)),
      remove: (...names) => names.forEach((n) => classes.delete(n)),
      toggle: (name, force) => {
        const shouldAdd = force === undefined ? !classes.has(name) : Boolean(force);
        if (shouldAdd) classes.add(name);
        else classes.delete(name);
        return shouldAdd;
      },
      contains: (name) => classes.has(name),
    },
    addEventListener(type, fn) {
      const arr = listeners.get(type) || [];
      arr.push(fn);
      listeners.set(type, arr);
    },
    async dispatch(type, eventInit = {}) {
      const arr = listeners.get(type) || [];
      const evt = {
        type,
        target: this,
        preventDefault() {
          this.defaultPrevented = true;
        },
        stopPropagation() {},
        ...eventInit,
      };
      for (const fn of arr) {
        await fn(evt);
      }
      return evt;
    },
    focus() {
      this._focused = true;
    },
    querySelector: () => null,
    querySelectorAll: () => [],
    setAttribute() {},
    getAttribute: () => null,
  };
}

function createSuppressionFixture(overridesPatch = {}) {
  const curatedOverrides = {
    "0621100000014": {
      id: "0621100000014",
      hcad_num: "0621100000014",
      address: "1127 KEY ST",
      year_built: 0,
      decade: 0,
      original_hcad_year: 1920,
      suppress_only: true,
      geometry: null,
    },
    "1223580020001#demolished_annex": {
      id: "1223580020001#demolished_annex",
      building_id: "1223580020001#demolished_annex",
      hcad_num: "1223580020001",
      is_building_override: true,
      suppress_only: true,
      keep_shard_footprints: false,
      address: "7900 N ELDRIDGE PKWY",
      building_name: "Demolished Portable Annex",
      year_built: 0,
      decade: 0,
      geometry: null,
    },
    "1223580020001#main_building": {
      id: "1223580020001#main_building",
      building_id: "1223580020001#main_building",
      hcad_num: "1223580020001",
      is_building_override: true,
      keep_shard_footprints: true,
      replace_parcel_shards: false,
      address: "7900 N ELDRIDGE PKWY",
      building_name: "Cypress Ridge High School",
      year_built: 2002,
      decade: 2000,
    },
    ...overridesPatch,
  };

  const ctrl = {
    curatedOverrides,
    buildingsData: [
      {
        type: "Feature",
        geometry: SAMPLE_BUILDING_POLY,
        properties: {
          id: "0621100000014#b1",
          building_id: "0621100000014#b1",
          hcad_num: "0621100000014",
          address: "1127 KEY ST",
          year_built: 1920,
        },
      },
      {
        type: "Feature",
        geometry: SAMPLE_BUILDING_POLY,
        properties: {
          id: "0621100000014#b2",
          building_id: "0621100000014#b2",
          hcad_num: "0621100000014",
          address: "1127 KEY ST",
          year_built: 1920,
        },
      },
      {
        type: "Feature",
        geometry: SAMPLE_BUILDING_POLY,
        properties: {
          id: "1223580020001#demolished_annex",
          building_id: "1223580020001#demolished_annex",
          hcad_num: "1223580020001",
          address: "7900 N ELDRIDGE PKWY",
          year_built: 1985,
        },
      },
      {
        type: "Feature",
        geometry: SAMPLE_BUILDING_POLY,
        properties: {
          id: "1223580020001#main_building",
          building_id: "1223580020001#main_building",
          hcad_num: "1223580020001",
          address: "7900 N ELDRIDGE PKWY",
          year_built: 2002,
        },
      },
    ],
    buildingsFC: { type: "FeatureCollection", features: [] },
    pilotParcelsData: [
      {
        type: "Feature",
        geometry: SAMPLE_PARCEL_LOT_POLY,
        properties: {
          id: "pcl_0621100000014",
          hcad_num: "0621100000014",
          address: "1127 KEY ST",
          year_built: 1920,
          decade: 1920,
          use_category: "Residential",
          is_tax_parcel: true,
        },
      },
    ],
    parcelsData: [
      {
        type: "Feature",
        geometry: SAMPLE_PARCEL_LOT_POLY,
        properties: {
          id: "parcel_0621100000014",
          hcad_num: "0621100000014",
          address: "1127 KEY ST",
          year_built: 1920,
          decade: 1920,
          use_category: "Residential",
          is_tax_parcel: true,
        },
      },
      {
        type: "Feature",
        geometry: SAMPLE_PARCEL_LOT_POLY,
        properties: {
          id: "parcel_1223580020001",
          hcad_num: "1223580020001",
          address: "7900 N ELDRIDGE PKWY",
          year_built: 2002,
          decade: 2000,
          use_category: "Civic / Institutional",
          is_tax_parcel: true,
        },
      },
    ],
    overridesFC: { type: "FeatureCollection", features: [] },
  };
  return { curatedOverrides, ctrl };
}

test("UT-2a: isDemolishedVacantOverrideInput and parseOverridesFromSheetRows classify vacant vs replacement inputs", () => {
  for (const token of [0, "0", "Vacant", "Vacant lot", "Demolished", "none", "N/A"]) {
    assert.equal(
      isDemolishedVacantOverrideInput({ yearRaw: token }),
      true,
      `Expected token ${JSON.stringify(token)} to trigger demolished/vacant suppression`
    );
  }
  assert.equal(
    isDemolishedVacantOverrideInput({
      yearRaw: "",
      footprintIssue: DEMOLISHED_VACANT_ISSUE,
    }),
    true
  );
  assert.equal(
    isDemolishedVacantOverrideInput({
      yearRaw: "",
      sourceCitation: `${DEMOLISHED_VACANT_CITATION_TAG} | House demolished`,
    }),
    true
  );
  assert.equal(
    isDemolishedVacantOverrideInput({
      yearRaw: "2020",
      footprintIssue: DEMOLISHED_REPLACED_ISSUE,
    }),
    false
  );
  assert.equal(
    isDemolishedVacantOverrideInput({
      yearRaw: "1836",
      footprintIssue: DEMOLISHED_VACANT_ISSUE,
    }),
    false
  );
  assert.equal(
    isDemolishedVacantOverrideInput({
      yearRaw: "0",
      suppressOnly: false,
    }),
    false,
    "Explicit suppressOnly: false must prevent falsy 0 from triggering vacant suppression"
  );

  const csv = [
    "status,hcad_num,address,year_built,original_hcad_year,footprint_issue,source_type,source_citation,geometry_geojson",
    'Approved,0621100000014,1127 KEY ST,0,1920,,,,"{""type"":""Polygon"",""coordinates"":[[[-95.39,29.79],[-95.38,29.79],[-95.38,29.80],[-95.39,29.79]]]}"',
    "Approved,0621100000015,1129 KEY ST,Vacant lot,1924,,,,",
    "Approved,0621100000016,1131 KEY ST,Demolished,1926,,,,",
    `Approved,0621100000017,1133 KEY ST,,1928,${DEMOLISHED_VACANT_ISSUE},Aerial / Satellite Imagery,,`,
    `Approved,0621100000018,1135 KEY ST,,1930,,Footprint Issue: ${DEMOLISHED_VACANT_ISSUE},${DEMOLISHED_VACANT_CITATION_TAG},`,
    `Approved,0621100000019,1137 KEY ST,2021,1925,${DEMOLISHED_REPLACED_ISSUE},Aerial / Satellite Imagery,Replaced by 2021 duplex,`,
  ].join("\n");

  const parsed = parseOverridesFromSheetRows(parseCsvToObjects(csv));
  for (const hcad of [
    "0621100000014",
    "0621100000015",
    "0621100000016",
    "0621100000017",
    "0621100000018",
  ]) {
    assert.equal(parsed[hcad].suppress_only, true, `Expected suppress_only=true for ${hcad}`);
    assert.equal(parsed[hcad].year_built, 0);
    assert.equal(parsed[hcad].decade, 0);
    assert.equal(parsed[hcad].geometry, null);
  }
  assert.equal(parsed["0621100000019"].suppress_only, false);
  assert.equal(parsed["0621100000019"].year_built, 2021);
  assert.equal(parsed["0621100000019"].decade, 2020);
});

test("UT-2b: submitCorrectionSuggestion and submitAdminApprovedOverride share normalizeCorrectionSubmission and protect undated standing buildings", async () => {
  const prevLS = globalThis.localStorage;
  const prevSS = globalThis.sessionStorage;
  globalThis.localStorage = createInMemoryStorage();
  globalThis.sessionStorage = createInMemoryStorage();
  try {
    // Undated standing building (current_year_built = "" or 0) with non-year name/builder edit MUST NOT coerce suggested_year_built to 0!
    const undatedNameEdit = await submitCorrectionSuggestion({
      hcad_num: "0420090000018",
      address: "408 AVONDALE ST",
      current_year_built: "",
      suggested_year_built: "",
      building_name: "Avondale Grocery | Old Corner Store",
      builder: "Fretz Construction",
      permit_info: "Permit #9912",
      footprint_issue: "Polygon shape or orientation is inaccurate",
      footprint_notes: "Rotated 20 degrees.",
      source_citation: "1928 City Directory p. 114",
    });
    assert.equal(undatedNameEdit.record.suppress_only, false);
    assert.equal(undatedNameEdit.record.suggested_year_built, "");
    assert.equal(undatedNameEdit.record.building_name, "Avondale Grocery");
    assert.equal(undatedNameEdit.record.alt_names, "Old Corner Store");
    assert.match(undatedNameEdit.record.source_type, /^Footprint Issue:/);
    assert.match(undatedNameEdit.record.source_citation, /\[BUILDER \/ CONTRACTOR\]: Fretz Construction/);

    // Explicit vacant submission sets suggested_year_built = 0 and suppress_only = true
    const vacantSuggestion = await submitCorrectionSuggestion({
      hcad_num: "0621100000014",
      address: "1127 KEY ST",
      current_year_built: 1920,
      suggested_year_built: "0",
      footprint_issue: "",
      footprint_notes: "",
    });
    assert.equal(vacantSuggestion.record.suppress_only, true);
    assert.equal(vacantSuggestion.record.suggested_year_built, 0);
    assert.equal(vacantSuggestion.record.footprint_issue, DEMOLISHED_VACANT_ISSUE);
    assert.equal(
      vacantSuggestion.record.footprint_notes,
      "Building demolished; lot currently vacant."
    );
    assert.match(vacantSuggestion.record.source_citation, /\[DEMOLISHED \/ VACANT LOT - HIDE FOOTPRINT\]/);

    globalThis.sessionStorage.setItem(
      "ph_atlas_admin_session",
      JSON.stringify({ authorized: true, email: "curator@preservationhouston.org" })
    );
    const adminVacant = await submitAdminApprovedOverride({
      hcad_num: "0621100000014",
      address: "1127 KEY ST",
      current_year_built: 1920,
      suggested_year_built: "",
      footprint_issue: DEMOLISHED_VACANT_ISSUE,
    });
    assert.equal(adminVacant.override.suppress_only, true);
    assert.equal(adminVacant.override.year_built, 0);
    assert.equal(adminVacant.override.decade, 0);
    assert.equal(adminVacant.override.geometry, null);

    const adminReplaced = await submitAdminApprovedOverride({
      hcad_num: "0621100000019",
      address: "1137 KEY ST",
      building_name: "New Key Duplex | Key Townhome",
      builder: "Height Builders",
      permit_info: "Permit #2021-88",
      current_year_built: 1925,
      suggested_year_built: "2021",
      footprint_issue: DEMOLISHED_REPLACED_ISSUE,
    });
    assert.equal(adminReplaced.override.suppress_only, false);
    assert.equal(adminReplaced.override.year_built, 2021);
    assert.equal(adminReplaced.override.decade, 2020);
    assert.equal(adminReplaced.override.building_name, "New Key Duplex");
    assert.deepEqual(adminReplaced.override.alt_names, ["Key Townhome"]);
    assert.equal(adminReplaced.override.builder, "Height Builders");
    assert.equal(adminReplaced.override.original_permit, "Permit #2021-88");

    const normCheck = normalizeCorrectionSubmission({
      hcad_num: "0621100000019",
      building_name: "New Key Duplex | Key Townhome",
      suggested_year_built: "2021",
      footprint_issue: DEMOLISHED_REPLACED_ISSUE,
    });
    assert.equal(normCheck.primaryBuildingName, "New Key Duplex");
    assert.deepEqual(normCheck.altNames, ["Key Townhome"]);
  } finally {
    clearAdminSession();
    globalThis.localStorage = prevLS;
    globalThis.sessionStorage = prevSS;
  }
});

test("UT-2c: loadCuratedOverrides and mergeOverrideRecord preserve pre-suppress geometry and revive replacement structures", async () => {
  const prevFetch = globalThis.fetch;
  const prevLS = globalThis.localStorage;
  globalThis.localStorage = createInMemoryStorage();

  const activeSheetCsv = [
    "status,hcad_num,address,year_built,original_hcad_year,footprint_issue,source_citation",
    `Approved,0621100000014,1127 KEY ST,,1920,${DEMOLISHED_VACANT_ISSUE},${DEMOLISHED_VACANT_CITATION_TAG}`,
    `Approved,1223580020001#old_wing,7900 N ELDRIDGE PKWY,0,1982,${DEMOLISHED_VACANT_ISSUE},${DEMOLISHED_VACANT_CITATION_TAG}`,
  ].join("\n");

  globalThis.fetch = async (url) => {
    const u = String(url);
    if (u.includes("curated_overrides.json")) {
      return {
        ok: true,
        json: async () => ({
          version: 1,
          googleSheetCsvUrl: "https://example.org/sheet?output=csv",
          submissionWebhookUrl: "",
          overrides: {
            "0621100000014": {
              id: "0621100000014",
              hcad_num: "0621100000014",
              address: "1127 KEY ST",
              year_built: 1928,
              decade: 1920,
              geometry: SAMPLE_BUILDING_POLY,
            },
            "1223580020001#old_wing": {
              id: "1223580020001#old_wing",
              building_id: "1223580020001#old_wing",
              hcad_num: "1223580020001",
              is_building_override: true,
              keep_shard_footprints: true,
              replace_parcel_shards: false,
              address: "7900 N ELDRIDGE PKWY",
              year_built: 1982,
              decade: 1980,
            },
          },
        }),
      };
    }
    return {
      ok: true,
      text: async () => activeSheetCsv,
    };
  };

  try {
    const res1 = await loadCuratedOverrides();
    assert.equal(res1.overrides["0621100000014"].suppress_only, true);
    assert.equal(res1.overrides["0621100000014"].year_built, 0);
    assert.equal(res1.overrides["0621100000014"].geometry, null);
    assert.deepEqual(res1.overrides["0621100000014"]._pre_suppress_geometry, SAMPLE_BUILDING_POLY);

    assert.equal(res1.overrides["1223580020001#old_wing"].suppress_only, true);
    assert.equal(res1.overrides["1223580020001#old_wing"].keep_shard_footprints, false);
    assert.equal(res1.overrides["1223580020001#old_wing"].geometry, null);

    // Round 2: Later replacement year revives previously suppressed records and restores _pre_suppress_geometry
    const revivedWhole = mergeOverrideRecord(res1.overrides["0621100000014"], {
      id: "0621100000014",
      hcad_num: "0621100000014",
      year_built: 2024,
      decade: 2020,
      suppress_only: false,
    });
    assert.equal(revivedWhole.suppress_only, false);
    assert.equal(revivedWhole.year_built, 2024);
    assert.deepEqual(revivedWhole.geometry, SAMPLE_BUILDING_POLY);
    assert.equal("keep_shard_footprints" in revivedWhole, false);
  } finally {
    globalThis.fetch = prevFetch;
    globalThis.localStorage = prevLS;
  }
});

test("UT-3a: _applyCuratedOverridesInMemory non-destructively filters buildingsData and _buildShardLayerFilter isolates #<slug> sub-buildings", () => {
  const { ctrl } = createSuppressionFixture();
  AtlasMapController.prototype._applyCuratedOverridesInMemory.call(ctrl);

  assert.deepEqual(
    ctrl.buildingsData.map((f) => f.properties.building_id),
    ["1223580020001#main_building"]
  );
  assert.deepEqual(
    ctrl.overridesFC.features.map((f) => f.properties.building_id),
    ["1223580020001#main_building"]
  );

  const filterExpr = AtlasMapController.prototype._buildShardLayerFilter.call(ctrl, ["all"]);
  const filterJson = JSON.stringify(filterExpr);
  assert.match(filterJson, /"0621100000014"/);
  assert.match(filterJson, /"1223580020001#demolished_annex"/);
  assert.doesNotMatch(filterJson, /"1223580020001",/);
  assert.doesNotMatch(filterJson, /"1223580020001#main_building"/);

  // Reviving whole-parcel 0621100000014 restores its footprints from _rawBuildingsData without reload!
  ctrl.curatedOverrides["0621100000014"] = {
    ...ctrl.curatedOverrides["0621100000014"],
    year_built: 2024,
    decade: 2020,
    suppress_only: false,
  };
  AtlasMapController.prototype._applyCuratedOverridesInMemory.call(ctrl);
  assert.equal(
    ctrl.buildingsData.some((f) => f.properties.hcad_num === "0621100000014"),
    true,
    "Revived parcel building footprints must be restored from _rawBuildingsData"
  );
});

test("UT-3b: Tax parcel hydration marks whole-parcel suppressions Vacant / Exempt and cleanly strips _pre_vacant_use_category on revival", () => {
  const { curatedOverrides, ctrl } = createSuppressionFixture();
  AtlasMapController.prototype._applyCuratedOverridesInMemory.call(ctrl);

  for (const pcl of [ctrl.pilotParcelsData[0], ctrl.parcelsData[0]]) {
    assert.equal(pcl.properties.year_built, 0);
    assert.equal(pcl.properties.decade, 0);
    assert.equal(pcl.properties.use_category, "Vacant / Exempt");
    assert.equal(pcl.properties.is_demolished_vacant, true);
    assert.equal(pcl.properties.suppress_only, false);
  }
  assert.equal(ctrl.parcelsData[1].properties.year_built, 2002);
  assert.equal(ctrl.parcelsData[1].properties.use_category, "Civic / Institutional");
  assert.equal(Boolean(ctrl.parcelsData[1].properties.is_demolished_vacant), false);

  const minYearsMap = collectMinBuildingYearsByHcad([
    { properties: { hcad_num: "0621100000014", year_built: 1920, use_category: "Residential" } },
  ]);
  const dynamicVacantParcel = enrichParcelFeature(
    {
      type: "Feature",
      geometry: SAMPLE_PARCEL_LOT_POLY,
      properties: {
        HCAD_NUM: "0621100000014",
        address: "1127 KEY ST",
        year_built: 1920,
        state_class: "A1",
      },
    },
    {
      buildingYearsByHcad: minYearsMap,
      curatedOverrides,
      applyOverrideFn: applyOverrideToProperties,
    }
  );
  assert.equal(dynamicVacantParcel.properties.use_category, "Vacant / Exempt");
  assert.equal(dynamicVacantParcel.properties.is_demolished_vacant, true);
  assert.equal(dynamicVacantParcel.properties.building_count, 0);

  const revivedParcelProps = applyOverrideToProperties(ctrl.parcelsData[0].properties, {
    "0621100000014": {
      id: "0621100000014",
      hcad_num: "0621100000014",
      address: "1127 KEY ST",
      year_built: 2024,
      decade: 2020,
      suppress_only: false,
    },
  });
  assert.equal(revivedParcelProps.year_built, 2024);
  assert.equal(revivedParcelProps.is_demolished_vacant, false);
  assert.equal(revivedParcelProps.use_category, "Residential");
  assert.equal("_pre_vacant_use_category" in revivedParcelProps, false);
});

test("UT-3c: isEntitySuppressedByOverrides and selectFeatureByIdOrHcad enforce whole-parcel OR-precedence over #<slug> sub-building IDs", () => {
  const { curatedOverrides, ctrl } = createSuppressionFixture({
    // Even if a pre-existing sub-building override exists on a whole-parcel suppressed HCAD, whole-parcel suppression wins
    "0621100000014#b1": {
      id: "0621100000014#b1",
      building_id: "0621100000014#b1",
      hcad_num: "0621100000014",
      is_building_override: true,
      suppress_only: false,
      year_built: 1925,
    },
  });
  AtlasMapController.prototype._applyCuratedOverridesInMemory.call(ctrl);

  assert.equal(
    isEntitySuppressedByOverrides(curatedOverrides, {
      buildingId: "0621100000014#b1",
      hcadNum: "0621100000014",
    }),
    true
  );
  assert.equal(
    isEntitySuppressedByOverrides(curatedOverrides, {
      buildingId: "1223580020001#demolished_annex",
      hcadNum: "1223580020001",
    }),
    true
  );
  assert.equal(
    isEntitySuppressedByOverrides(curatedOverrides, {
      buildingId: "1223580020001#main_building",
      hcadNum: "1223580020001",
    }),
    false
  );

  const mergedOnWholeSuppressedSub = applyOverrideToProperties(
    {
      id: "0621100000014#b1",
      building_id: "0621100000014#b1",
      hcad_num: "0621100000014",
      year_built: 1925,
    },
    curatedOverrides
  );
  assert.equal(mergedOnWholeSuppressedSub.suppress_only, true);

  const inspected = [];
  let idleCb = null;
  const selectCtrl = {
    ...ctrl,
    useCanvasFallback: false,
    _selectNavSeq: 0,
    buildingFillLayerIds: ["buildings-fill-nw_e"],
    buildingExtrusionLayerIds: [],
    shardSourceIds: ["buildings-nw_e"],
    overlaysData: {},
    permitAlertsPayload: { alerts: [] },
    getPlatWatchForBuilding: () => null,
    flyToLocation: () => {},
    highlightAndInspectFeature(props, geom) {
      inspected.push({ props, geom });
    },
    map: {
      getLayer: (id) => id === "buildings-fill-nw_e",
      project: () => ({ x: 100, y: 100 }),
      once: (evt, cb) => {
        if (evt === "idle") idleCb = cb;
      },
      queryRenderedFeatures: () => [],
      querySourceFeatures: () => [
        {
          type: "Feature",
          geometry: SAMPLE_BUILDING_POLY,
          properties: {
            id: "0621100000014#b1",
            building_id: "0621100000014#b1",
            hcad_num: "0621100000014",
            address: "1127 KEY ST",
            year_built: 1920,
          },
        },
      ],
    },
  };

  // Querying a #<slug> sub-building ID on a whole-parcel suppressed HCAD MUST return false (no ghost fallback card!)
  const okWholeWithSubId = AtlasMapController.prototype.selectFeatureByIdOrHcad.call(selectCtrl, {
    buildingId: "0621100000014#b1",
    hcadNum: "0621100000014",
    addressQuery: "1127 KEY ST",
    targetLng: -95.39,
    targetLat: 29.79,
  });
  if (idleCb) idleCb();
  assert.equal(okWholeWithSubId, false);
  assert.equal(inspected.length, 0);

  const okSubSuppressed = AtlasMapController.prototype.selectFeatureByIdOrHcad.call(selectCtrl, {
    hcadNum: "1223580020001",
    featureId: "1223580020001#demolished_annex",
    addressQuery: "7900 N ELDRIDGE PKWY",
    targetLng: -95.6,
    targetLat: 29.89,
  });
  if (idleCb) idleCb();
  assert.equal(okSubSuppressed, false);
  assert.equal(inspected.length, 0);

  const okSibling = AtlasMapController.prototype.selectFeatureByIdOrHcad.call(selectCtrl, {
    hcadNum: "1223580020001",
    featureId: "1223580020001#main_building",
    addressQuery: "7900 N ELDRIDGE PKWY",
  });
  assert.equal(okSibling, true);
  assert.equal(inspected.length, 1);
  assert.equal(inspected[0].props.building_id, "1223580020001#main_building");
});

test("UT-3d: _mergeCuratedOverridesIntoSearchIndex and _runSearchQuery suppress demolished entries across searchIndex and address_shards", () => {
  const { curatedOverrides } = createSuppressionFixture();
  const searchResultsEl = createMockElement("search-results");
  const searchInputEl = createMockElement("search-input", { value: "1127 KEY ST" });
  const prevDoc = globalThis.document;
  globalThis.document = {
    getElementById: (id) => {
      if (id === "search-results") return searchResultsEl;
      if (id === "search-input") return searchInputEl;
      return null;
    },
    querySelectorAll: () => [],
  };

  try {
    const fakeApp = {
      filterStore: createFilterStore(),
      mapController: {
        curatedOverrides,
        getRenderedBuildingCandidates: () => [],
      },
      searchIndex: [
        {
          type: "building",
          id: "0621100000014#b1",
          building_id: "0621100000014#b1",
          hcad_num: "0621100000014",
          label: "1127 KEY ST",
          address: "1127 KEY ST",
          year_built: 1920,
          lon: -95.39,
          lat: 29.79,
        },
        {
          type: "building",
          id: "1223580020001#demolished_annex",
          building_id: "1223580020001#demolished_annex",
          hcad_num: "1223580020001",
          label: "Demolished Portable Annex",
          address: "7900 N ELDRIDGE PKWY",
          year_built: 1985,
          lon: -95.6,
          lat: 29.89,
        },
        {
          type: "building",
          id: "1223580020001#main_building",
          building_id: "1223580020001#main_building",
          hcad_num: "1223580020001",
          label: "Cypress Ridge High School",
          building_name: "Cypress Ridge High School",
          address: "7900 N ELDRIDGE PKWY",
          year_built: 2002,
          lon: -95.604,
          lat: 29.892,
        },
      ],
      _addressShardCache: {
        "11": {
          "KEY ST": [
            ["1127", -95.39, 29.79, 1920, "0621100000014", "", "0621100000014#b1"],
            ["1129", -95.391, 29.79, 1924, "0621100000015", "", "0621100000015#b1"],
          ],
        },
        "79": {
          "N ELDRIDGE PKWY": [
            [
              "7900",
              -95.6,
              29.89,
              1985,
              "1223580020001",
              "Demolished Portable Annex",
              "1223580020001#demolished_annex",
            ],
            [
              "7900",
              -95.604,
              29.892,
              2002,
              "1223580020001",
              "Cypress Ridge High School",
              "1223580020001#main_building",
            ],
          ],
        },
      },
      _addressShardPending: {},
      _isSuppressedByCuratedOverride: HoustonAtlasApp.prototype._isSuppressedByCuratedOverride,
      _getSearchLowercaseCache: HoustonAtlasApp.prototype._getSearchLowercaseCache,
      _escapeHtmlAttr: HoustonAtlasApp.prototype._escapeHtmlAttr,
    };

    HoustonAtlasApp.prototype._mergeCuratedOverridesIntoSearchIndex.call(fakeApp);
    assert.deepEqual(
      fakeApp.searchIndex.map((it) => it.building_id || it.id),
      ["1223580020001#main_building"]
    );

    HoustonAtlasApp.prototype._runSearchQuery.call(fakeApp, "1127 KEY ST");
    assert.doesNotMatch(searchResultsEl.innerHTML, /0621100000014|1127 KEY ST.*Built 1920/);

    searchInputEl.value = "7900 N ELDRIDGE PKWY";
    HoustonAtlasApp.prototype._runSearchQuery.call(fakeApp, "7900 N ELDRIDGE PKWY");
    assert.doesNotMatch(searchResultsEl.innerHTML, /demolished_annex|Demolished Portable Annex/);
    assert.match(searchResultsEl.innerHTML, /Cypress Ridge High School/);
  } finally {
    globalThis.document = prevDoc;
  }
});

test("UT-4: #correction-modal bidirectional sync, validation, 1-click Admin suppression, and tax-parcel revival geometry safety", async () => {
  const prevDoc = globalThis.document;
  const prevWin = globalThis.window;
  const prevLS = globalThis.localStorage;
  const prevSS = globalThis.sessionStorage;
  const prevFetch = globalThis.fetch;

  globalThis.localStorage = createInMemoryStorage();
  globalThis.sessionStorage = createInMemoryStorage();

  const elements = {
    "correction-modal": createMockElement("correction-modal", { classes: ["hidden"] }),
    "form-property-correction": createMockElement("form-property-correction"),
    "corr-address": createMockElement("corr-address"),
    "corr-building-name": createMockElement("corr-building-name"),
    "corr-hcad-num": createMockElement("corr-hcad-num"),
    "corr-current-year": createMockElement("corr-current-year"),
    "corr-suggested-year": createMockElement("corr-suggested-year"),
    "corr-district": createMockElement("corr-district"),
    "corr-footprint-box": createMockElement("corr-footprint-box"),
    "corr-footprint-issue": createMockElement("corr-footprint-issue"),
    "corr-footprint-notes-wrap": createMockElement("corr-footprint-notes-wrap", {
      classes: ["hidden"],
    }),
    "lbl-corr-footprint-notes": createMockElement("lbl-corr-footprint-notes"),
    "corr-footprint-notes": createMockElement("corr-footprint-notes"),
    "corr-source-type": createMockElement("corr-source-type", { value: "Houston City Directory" }),
    "corr-style-arch": createMockElement("corr-style-arch"),
    "corr-builder": createMockElement("corr-builder"),
    "corr-permit-info": createMockElement("corr-permit-info"),
    "corr-citation": createMockElement("corr-citation"),
    "corr-photo-url": createMockElement("corr-photo-url"),
    "corr-photo-year": createMockElement("corr-photo-year"),
    "corr-photo-caption": createMockElement("corr-photo-caption"),
    "corr-photo-details": createMockElement("corr-photo-details"),
    "corr-deed-subdivision": createMockElement("corr-deed-subdivision"),
    "corr-deed-clerk-file": createMockElement("corr-deed-clerk-file"),
    "corr-deed-url": createMockElement("corr-deed-url"),
    "corr-deed-details": createMockElement("corr-deed-details"),
    "corr-submit-feedback": createMockElement("corr-submit-feedback", { classes: ["hidden"] }),
    "btn-submit-correction": createMockElement("btn-submit-correction"),
    "btn-admin-approve-direct": createMockElement("btn-admin-approve-direct", {
      classes: ["hidden"],
    }),
    "admin-webhook-url": createMockElement("admin-webhook-url", {
      value: "https://example.org/webhook",
    }),
  };

  globalThis.document = {
    getElementById: (id) => elements[id] || null,
    querySelectorAll: () => [],
    addEventListener: () => {},
  };
  globalThis.window = {
    addEventListener: () => {},
    location: { pathname: "/index.html", search: "", hash: "" },
  };

  const webhookRequests = [];
  globalThis.fetch = async (url, opts = {}) => {
    webhookRequests.push({ url: String(url), body: opts.body ? JSON.parse(opts.body) : null });
    return {
      ok: true,
      json: async () => ({ ok: true, status: "Approved", authorized: true }),
    };
  };

  try {
    let clearedCount = 0;
    let refreshedCount = 0;
    const fakeCtrl = {
      curatedOverrides: {
        "0621100000014": {
          id: "0621100000014",
          hcad_num: "0621100000014",
          address: "1127 KEY ST",
          year_built: 1928,
          decade: 1920,
          is_curated_override: true,
          geometry: SAMPLE_BUILDING_POLY,
        },
      },
      buildingsData: [
        {
          type: "Feature",
          geometry: SAMPLE_BUILDING_POLY,
          properties: {
            id: "0621100000014#b1",
            building_id: "0621100000014#b1",
            hcad_num: "0621100000014",
            address: "1127 KEY ST",
            year_built: 1928,
          },
        },
      ],
      parcelsData: [
        {
          type: "Feature",
          geometry: SAMPLE_PARCEL_LOT_POLY,
          properties: {
            id: "parcel_0621100000014",
            hcad_num: "0621100000014",
            address: "1127 KEY ST",
            year_built: 1928,
            decade: 1920,
            use_category: "Residential",
            is_tax_parcel: true,
          },
        },
      ],
      pilotParcelsData: [],
      overridesFC: { type: "FeatureCollection", features: [] },
      selectedFeatureProps: {
        id: "0621100000014",
        hcad_num: "0621100000014",
        address: "1127 KEY ST",
        year_built: 1928,
        original_hcad_year: 1920,
        is_curated_override: true,
      },
      selectedFeatureGeometry: SAMPLE_BUILDING_POLY,
      sheetSyncStatus: { csvUrl: "", webhookUrl: "https://example.org/webhook" },
      _refreshCuratedOverridesSource() {
        refreshedCount += 1;
        AtlasMapController.prototype._applyCuratedOverridesInMemory.call(this);
      },
      clearSelection() {
        clearedCount += 1;
        this.selectedFeatureProps = null;
        this.selectedFeatureGeometry = null;
      },
      highlightAndInspectFeature(props, geom) {
        this.selectedFeatureProps = props;
        this.selectedFeatureGeometry = geom;
      },
    };

    const fakeApp = {
      filterStore: createFilterStore(),
      mapController: fakeCtrl,
      searchIndex: [
        {
          type: "building",
          id: "0621100000014",
          hcad_num: "0621100000014",
          label: "1127 KEY ST",
          address: "1127 KEY ST",
          year_built: 1928,
        },
      ],
      _syncInspectorLayoutControls: () => {},
      _renderSharePresetsGrid: () => {},
      _bindExportStudioControls: () => {},
      _resolveBuildingNameAndAliases: () => ({
        primaryName: "",
        altNames: [],
        hasCustomBuildingName: false,
      }),
      _computeGeometryCentroid: () => [-95.39, 29.79],
      _mergeCuratedOverridesIntoSearchIndex:
        HoustonAtlasApp.prototype._mergeCuratedOverridesIntoSearchIndex,
      _enrichSearchIndexWithOverlayMetadata: () => {},
    };

    globalThis.sessionStorage.setItem(
      "ph_atlas_admin_session",
      JSON.stringify({ authorized: true, email: "admin@preservationhouston.org" })
    );

    HoustonAtlasApp.prototype._bindControls.call(fakeApp);
    assert.equal(elements["btn-admin-approve-direct"].classList.contains("hidden"), false);

    HoustonAtlasApp.prototype.openCorrectionModal.call(fakeApp, fakeCtrl.selectedFeatureProps);
    assert.equal(String(elements["corr-suggested-year"].value), "1928");

    // Direction A: Selecting "Building demolished - lot currently vacant" overwrites pre-filled 1928 with "0"
    elements["corr-footprint-issue"].value = DEMOLISHED_VACANT_ISSUE;
    await elements["corr-footprint-issue"].dispatch("change");
    assert.equal(elements["corr-suggested-year"].value, "0");
    assert.equal(elements["corr-footprint-notes"].required, false);

    // Switching to "Building demolished & replaced by newer structure" clears "0" and makes notes required
    elements["corr-footprint-issue"].value = DEMOLISHED_REPLACED_ISSUE;
    await elements["corr-footprint-issue"].dispatch("change");
    assert.equal(elements["corr-suggested-year"].value, "");
    assert.equal(elements["corr-footprint-notes"].required, true);

    // Direction B: Typing "0" auto-selects DEMOLISHED_VACANT_ISSUE
    elements["corr-footprint-issue"].value = "";
    await elements["corr-footprint-issue"].dispatch("change");
    elements["corr-suggested-year"].value = "0";
    await elements["corr-suggested-year"].dispatch("input");
    assert.equal(elements["corr-footprint-issue"].value, DEMOLISHED_VACANT_ISSUE);
    assert.equal(elements["corr-footprint-notes"].required, false);

    // Validation rejects years 1..1835 and >2026 on public submit
    elements["corr-footprint-issue"].value = "";
    for (const badYear of ["1", "1800", "1835", "2027", "-5"]) {
      const beforeCount = webhookRequests.length;
      elements["corr-suggested-year"].value = badYear;
      await elements["form-property-correction"].dispatch("submit");
      assert.equal(webhookRequests.length, beforeCount);
      assert.match(elements["corr-submit-feedback"].innerHTML, /1836 and 2026/);
    }

    // Valid year 0 with blank notes defaults notes to "Building demolished; lot currently vacant."
    elements["corr-footprint-issue"].value = "";
    elements["corr-footprint-notes"].value = "";
    elements["corr-suggested-year"].value = "0";
    await elements["corr-suggested-year"].dispatch("input");
    await elements["form-property-correction"].dispatch("submit");
    assert.equal(webhookRequests.length, 1);
    assert.equal(webhookRequests[0].body.suppress_only, true);
    assert.equal(webhookRequests[0].body.suggested_year_built, 0);

    // 1-Click Staff Admin #btn-admin-approve-direct live vacant-lot footprint suppression
    elements["corr-footprint-issue"].value = DEMOLISHED_VACANT_ISSUE;
    await elements["corr-footprint-issue"].dispatch("change");
    elements["corr-footprint-notes"].value = "";
    await elements["btn-admin-approve-direct"].dispatch("click");

    assert.equal(webhookRequests.length, 2);
    assert.equal(webhookRequests[1].body.action, "admin_approve");
    assert.equal(webhookRequests[1].body.suppress_only, true);
    assert.equal(refreshedCount, 1);
    assert.equal(clearedCount, 1);
    assert.equal(fakeCtrl.curatedOverrides["0621100000014"].suppress_only, true);
    assert.equal(fakeCtrl.curatedOverrides["0621100000014"].geometry, null);
    assert.equal(fakeCtrl.buildingsData.length, 0);
    assert.equal(fakeApp.searchIndex.length, 0);
    assert.equal(fakeCtrl.parcelsData[0].properties.use_category, "Vacant / Exempt");

    // Clicking the vacant tax parcel (is_tax_parcel: true, geometry = SAMPLE_PARCEL_LOT_POLY) and approving a replacement year (2024)
    // MUST restore the building footprint geometry (SAMPLE_BUILDING_POLY) and NEVER copy the tax parcel lot boundary!
    fakeCtrl.highlightAndInspectFeature(
      fakeCtrl.parcelsData[0].properties,
      fakeCtrl.parcelsData[0].geometry
    );
    HoustonAtlasApp.prototype.openCorrectionModal.call(fakeApp, fakeCtrl.selectedFeatureProps);
    assert.equal(elements["corr-suggested-year"].value, "0");
    assert.equal(elements["corr-footprint-issue"].value, DEMOLISHED_VACANT_ISSUE);

    elements["corr-footprint-issue"].value = DEMOLISHED_REPLACED_ISSUE;
    await elements["corr-footprint-issue"].dispatch("change");
    elements["corr-suggested-year"].value = "2024";
    elements["corr-footprint-notes"].value = "New infill residence built 2024.";
    await elements["btn-admin-approve-direct"].dispatch("click");

    assert.equal(fakeCtrl.curatedOverrides["0621100000014"].suppress_only, false);
    assert.equal(fakeCtrl.curatedOverrides["0621100000014"].year_built, 2024);
    assert.deepEqual(
      fakeCtrl.curatedOverrides["0621100000014"].geometry,
      SAMPLE_BUILDING_POLY,
      "Reviving a clicked vacant tax parcel must restore the building footprint polygon, never the full tax lot boundary"
    );
    assert.notDeepEqual(
      fakeCtrl.curatedOverrides["0621100000014"].geometry,
      SAMPLE_PARCEL_LOT_POLY
    );
    assert.equal(fakeCtrl.buildingsData.length, 1);
    assert.equal(fakeCtrl.parcelsData[0].properties.is_demolished_vacant, false);
    assert.equal(fakeCtrl.parcelsData[0].properties.use_category, "Residential");
  } finally {
    clearAdminSession();
    globalThis.document = prevDoc;
    globalThis.window = prevWin;
    globalThis.localStorage = prevLS;
    globalThis.sessionStorage = prevSS;
    globalThis.fetch = prevFetch;
  }
});
