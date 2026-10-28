import test from "node:test";
import assert from "node:assert/strict";

import { HoustonAtlasApp } from "../../app/js/app.js";
import { AtlasMapController } from "../../app/js/mapController.js";
import {
  buildShareableUrl,
  createFilterStore,
  normalizeComparableAddress,
} from "../../app/js/filterStore.js";

test("UT-3 & REG-1: _mergeCuratedOverridesIntoSearchIndex preserves school_* and sibling #<slug> entries while merging 1:1 single-building HCADs", () => {
  const fakeApp = {
    mapController: {
      curatedOverrides: {
        // Multi-building split campus #1: Cypress Ridge HS + Eldridge Transportation Center + aux support building
        "1223580020001#cy_ridge_hs_main": {
          id: "1223580020001#cy_ridge_hs_main",
          building_id: "1223580020001#cy_ridge_hs_main",
          hcad_num: "1223580020001",
          is_building_override: true,
          keep_shard_footprints: true,
          replace_parcel_shards: false,
          building_name: "Cypress Ridge High School",
          address: "7900 N ELDRIDGE PKWY",
          year_built: 2002,
          decade: 2000,
          architect: "PBK Architects",
          bld_style: "Contemporary Educational Complex",
          lon: -95.60458,
          lat: 29.891962,
        },
        "1223580020001#eldridge_trans_main": {
          id: "1223580020001#eldridge_trans_main",
          building_id: "1223580020001#eldridge_trans_main",
          hcad_num: "1223580020001",
          is_building_override: true,
          keep_shard_footprints: true,
          replace_parcel_shards: false,
          building_name: "Cy-Fair ISD Eldridge Transportation Center",
          address: "7600 N ELDRIDGE PKWY",
          year_built: 2002,
          decade: 2000,
          bld_style: "District Transportation & Fleet Maintenance Facility",
          lon: -95.603476,
          lat: 29.888208,
        },
        "1223580020001#cy_ridge_hs_aux_1": {
          id: "1223580020001#cy_ridge_hs_aux_1",
          building_id: "1223580020001#cy_ridge_hs_aux_1",
          hcad_num: "1223580020001",
          is_building_override: true,
          keep_shard_footprints: true,
          replace_parcel_shards: false,
          skip_search_index: true,
          building_name: "Cypress Ridge High School - Athletic & Campus Support Building",
          address: "7900 N ELDRIDGE PKWY",
          year_built: 2002,
          decade: 2000,
          lon: -95.6051,
          lat: 29.8924,
        },
        // Single-building 13-digit HCAD override (REG-1)
        "0010330000018": {
          hcad_num: "0010330000018",
          building_name: "Sweeney, Coombs & Fredericks Building",
          address: "301 MAIN ST",
          year_built: 1889,
          decade: 1880,
          architect: "George E. Dickey",
          bld_style: "Victorian Romanesque Revival",
        },
      },
    },
    searchIndex: [
      {
        type: "building",
        id: "school_612",
        building_id: "1223580020001#cy_ridge_hs_main",
        hcad_num: "1223580020001",
        label: "CYPRESS RIDGE High School",
        address: "0 N ELDRIDGE PKWY",
        year_built: 1900,
        lon: -95.60502,
        lat: 29.89291,
      },
      {
        type: "building",
        id: "1223580020001#cy_ridge_hs_main",
        building_id: "1223580020001#cy_ridge_hs_main",
        hcad_num: "1223580020001",
        label: "Cypress Ridge High School",
        building_name: "Cypress Ridge High School",
        address: "7900 N ELDRIDGE PKWY",
        year_built: 2002,
        lon: -95.60458,
        lat: 29.891962,
      },
      // Co-located charter campus sharing one HCAD across 3 distinct school_* records
      {
        type: "building",
        id: "school_565",
        building_id: "1212000020001#b1",
        hcad_num: "1212000020001",
        label: "Kipp Academy Middle",
        address: "10711 KIPP WAY ST",
        year_built: 2005,
        lon: -95.56683,
        lat: 29.67099,
      },
      {
        type: "building",
        id: "school_566",
        building_id: "1212000020001#b1",
        hcad_num: "1212000020001",
        label: "KIPP HOUSTON High School",
        address: "10711 KIPP WAY ST",
        year_built: 2005,
        lon: -95.56683,
        lat: 29.67099,
      },
      {
        type: "building",
        id: "school_567",
        building_id: "1212000020001#b1",
        hcad_num: "1212000020001",
        label: "Kipp Shine Prep",
        address: "10711 KIPP WAY ST",
        year_built: 2005,
        lon: -95.56683,
        lat: 29.67099,
      },
      // Existing single-building row for REG-1 1:1 in-place merge
      {
        type: "building",
        id: "0010330000018#b1",
        hcad_num: "0010330000018",
        label: "301 MAIN ST",
        address: "301 MAIN ST",
        year_built: 1890,
        neighborhood: "Downtown",
        lon: -95.3621,
        lat: 29.7631,
      },
    ],
  };

  HoustonAtlasApp.prototype._mergeCuratedOverridesIntoSearchIndex.call(fakeApp);

  const byId = new Map(fakeApp.searchIndex.map((it) => [it.id, it]));

  // 1. school_612 is preserved AND hydrated with verified year, architect, style, and numbered street situs
  const sch612 = byId.get("school_612");
  assert.ok(sch612, "school_612 must remain in searchIndex");
  assert.equal(sch612.building_id, "1223580020001#cy_ridge_hs_main");
  assert.equal(sch612.year_built, 2002);
  assert.equal(sch612.architect, "PBK Architects");
  assert.equal(sch612.address, "7900 N ELDRIDGE PKWY");

  // 2. Both primary #<slug> buildings on 1223580020001 coexist independently without clobbering each other
  const cyMain = byId.get("1223580020001#cy_ridge_hs_main");
  const busBarn = byId.get("1223580020001#eldridge_trans_main");
  assert.ok(cyMain, "1223580020001#cy_ridge_hs_main must exist as its own search entry");
  assert.ok(busBarn, "1223580020001#eldridge_trans_main must exist as its own search entry");
  assert.equal(cyMain.address, "7900 N ELDRIDGE PKWY");
  assert.equal(busBarn.address, "7600 N ELDRIDGE PKWY");

  // 3. Auxiliary skip_search_index buildings are NOT pushed as duplicate/Houston-Heights-teleported search entries
  assert.equal(
    byId.has("1223580020001#cy_ridge_hs_aux_1"),
    false,
    "Auxiliary skip_search_index campus sheds must not pollute searchIndex"
  );
  for (const item of fakeApp.searchIndex) {
    assert.notEqual(item.lon, -95.38718, `Search item ${item.id} fell back to hardcoded Houston Heights lon`);
  }

  // 4. Co-located schools sharing one HCAD all survive intact
  assert.ok(byId.has("school_565") && byId.has("school_566") && byId.has("school_567"));

  // 5. REG-1: Single-building 13-digit HCAD override merges 1:1 in-place without duplicates or neighborhood loss
  const sweeneyRows = fakeApp.searchIndex.filter((it) => it.hcad_num === "0010330000018");
  assert.equal(sweeneyRows.length, 1, "Single-building 13-digit HCAD override must merge 1:1 without duplication");
  assert.equal(sweeneyRows[0].year_built, 1889);
  assert.equal(sweeneyRows[0].architect, "George E. Dickey");
  assert.equal(sweeneyRows[0].neighborhood, "Downtown");
});

test("UT-3: _resolveAndSelectDeepLinkedProperty resolves #<slug> building_id, school_* IDs, and versioned address_shards row[6]", async () => {
  const fetchedUrls = [];
  const prevFetch = globalThis.fetch;
  const prevDoc = globalThis.document;
  globalThis.document = {
    getElementById: () => null,
  };
  globalThis.fetch = async (url) => {
    fetchedUrls.push(String(url));
    return {
      ok: true,
      json: async () => ({
        "N ELDRIDGE PKWY": [
          [
            "7600",
            -95.6034,
            29.8882,
            0,
            "",
            "Cy-Fair ISD Eldridge Transportation Center",
          ],
          [
            "7600",
            -95.6039,
            29.8888,
            2002,
            "1223580020001",
            "Eldridge Bus Barn Support 3",
            "1223580020001#eldridge_trans_aux_3",
          ],
          [
            "7600",
            -95.603476,
            29.888208,
            2002,
            "1223580020001",
            "Cy-Fair ISD Eldridge Transportation Center",
            "1223580020001#eldridge_trans_main",
          ],
        ],
      }),
    };
  };

  try {
    const selectCalls = [];
    const fakeApp = {
      _addressShardCache: {},
      searchIndex: [
        {
          type: "building",
          id: "school_612",
          building_id: "1223580020001#cy_ridge_hs_main",
          hcad_num: "1223580020001",
          label: "CYPRESS RIDGE High School",
          building_name: "Cypress Ridge High School",
          address: "7900 N ELDRIDGE PKWY",
          year_built: 2002,
          lon: -95.60502,
          lat: 29.89291,
        },
      ],
      mapController: {
        curatedOverrides: {
          "1223580020001#cy_ridge_hs_aux_1": {
            id: "1223580020001#cy_ridge_hs_aux_1",
            building_id: "1223580020001#cy_ridge_hs_aux_1",
            hcad_num: "1223580020001",
            building_name: "Cypress Ridge High School - Athletic & Campus Support Building",
            address: "7900 N ELDRIDGE PKWY",
            year_built: 2002,
            lon: -95.6051,
            lat: 29.8924,
          },
        },
        selectFeatureByIdOrHcad: (arg) => {
          selectCalls.push(arg);
        },
        flyToLocation: (arg) => {
          selectCalls.push({ viaFlyTo: true, ...arg });
        },
      },
    };

    // Case A: Deep link by #id=1223580020001#cy_ridge_hs_main (matched via searchIndex item.building_id!)
    await HoustonAtlasApp.prototype._resolveAndSelectDeepLinkedProperty.call(
      fakeApp,
      { hcadNum: "", featureId: "1223580020001#cy_ridge_hs_main", query: "" },
      null,
      false
    );
    assert.equal(selectCalls.length, 1);
    assert.equal(selectCalls[0].featureId, "1223580020001#cy_ridge_hs_main");
    assert.equal(selectCalls[0].hcadNum, "1223580020001");
    assert.equal(selectCalls[0].targetLng, -95.60502);
    assert.equal(selectCalls[0].targetLat, 29.89291);

    // Case B: Deep link by #id=school_612 upgrades featureId to building_id (1223580020001#cy_ridge_hs_main)
    await HoustonAtlasApp.prototype._resolveAndSelectDeepLinkedProperty.call(
      fakeApp,
      { hcadNum: "", featureId: "school_612", query: "" },
      null,
      false
    );
    assert.equal(selectCalls.length, 2);
    assert.equal(selectCalls[1].featureId, "1223580020001#cy_ridge_hs_main");

    // Case C: Deep link by #q=7600 N ELDRIDGE PKWY fetches versioned addr_76.json?v=20261028b and extracts row[6] building_id
    await HoustonAtlasApp.prototype._resolveAndSelectDeepLinkedProperty.call(
      fakeApp,
      { hcadNum: "", featureId: "", query: "7600 N ELDRIDGE PKWY" },
      null,
      false
    );
    assert.equal(selectCalls.length, 3);
    assert.ok(
      fetchedUrls.some((u) => u === "public/data/address_shards/addr_76.json?v=20261028b"),
      `Expected versioned addr_76.json?v=20261028b fetch, got ${JSON.stringify(fetchedUrls)}`
    );
    assert.equal(selectCalls[2].featureId, "1223580020001#eldridge_trans_main");
    assert.equal(selectCalls[2].targetLng, -95.603476);

    // Case D: Deep link to auxiliary #<slug> override not in searchIndex resolves coordinates from curatedOverrides
    await HoustonAtlasApp.prototype._resolveAndSelectDeepLinkedProperty.call(
      fakeApp,
      { hcadNum: "", featureId: "1223580020001#cy_ridge_hs_aux_1", query: "" },
      null,
      false
    );
    assert.equal(selectCalls.length, 4);
    assert.equal(selectCalls[3].featureId, "1223580020001#cy_ridge_hs_aux_1");
    assert.equal(selectCalls[3].targetLng, -95.6051);
    assert.equal(selectCalls[3].targetLat, 29.8924);
  } finally {
    globalThis.fetch = prevFetch;
    globalThis.document = prevDoc;
  }
});

test("UT-3: selectFeatureByIdOrHcad prioritizes Pass 1 (candidateBldIds) over Pass 2 (candidateHcads) and preserves fallbackBuildingName", () => {
  const cyPoly = {
    type: "Polygon",
    coordinates: [
      [
        [-95.605, 29.892],
        [-95.604, 29.892],
        [-95.604, 29.893],
        [-95.605, 29.893],
        [-95.605, 29.892],
      ],
    ],
  };
  const busPoly = {
    type: "Polygon",
    coordinates: [
      [
        [-95.6035, 29.888],
        [-95.603, 29.888],
        [-95.603, 29.8885],
        [-95.6035, 29.8885],
        [-95.6035, 29.888],
      ],
    ],
  };

  const inspected = [];
  const fakeCtrl = {
    useCanvasFallback: false,
    filterStore: createFilterStore(),
    buildingFillLayerIds: ["buildings-fill-0"],
    buildingExtrusionLayerIds: [],
    shardSourceIds: ["buildings-shard-0"],
    curatedOverrides: {
      "1223580020001#cy_ridge_hs_main": {
        id: "1223580020001#cy_ridge_hs_main",
        building_id: "1223580020001#cy_ridge_hs_main",
        hcad_num: "1223580020001",
        is_building_override: true,
        building_name: "Cypress Ridge High School",
        address: "7900 N ELDRIDGE PKWY",
        year_built: 2002,
      },
      "1223580020001#eldridge_trans_main": {
        id: "1223580020001#eldridge_trans_main",
        building_id: "1223580020001#eldridge_trans_main",
        hcad_num: "1223580020001",
        is_building_override: true,
        building_name: "Cy-Fair ISD Eldridge Transportation Center",
        address: "7600 N ELDRIDGE PKWY",
        year_built: 2002,
      },
    },
    overridesFC: { type: "FeatureCollection", features: [] },
    buildingsData: [],
    overlaysData: {},
    getPlatWatchForBuilding: () => null,
    highlightAndInspectFeature: (props, geom) => {
      inspected.push({ props, geom });
    },
    flyToLocation: () => {},
    map: {
      getLayer: () => true,
      once: () => {},
      queryRenderedFeatures: () => [
        // Deliberately put Eldridge Bus Barn FIRST in rendered features array so naive hcad_num matching would pick the wrong building!
        {
          type: "Feature",
          geometry: busPoly,
          properties: {
            id: "1223580020001#eldridge_trans_main",
            building_id: "1223580020001#eldridge_trans_main",
            hcad_num: "1223580020001",
            address: "7600 N ELDRIDGE PKWY",
            year_built: 2002,
          },
        },
        {
          type: "Feature",
          geometry: cyPoly,
          properties: {
            id: "1223580020001#cy_ridge_hs_main",
            building_id: "1223580020001#cy_ridge_hs_main",
            hcad_num: "1223580020001",
            address: "7900 N ELDRIDGE PKWY",
            year_built: 2002,
          },
        },
        {
          type: "Feature",
          geometry: cyPoly,
          properties: {
            id: "0660460010001#b1",
            building_id: "0660460010001#b1",
            hcad_num: "0660460010001",
            address: "5100 MAPLE ST",
            year_built: 1955,
          },
        },
      ],
      querySourceFeatures: () => [],
    },
  };

  // Pass 1 selects Cypress Ridge HS despite Eldridge Bus Barn appearing earlier with identical hcad_num
  const ok = AtlasMapController.prototype.selectFeatureByIdOrHcad.call(fakeCtrl, {
    hcadNum: "1223580020001",
    featureId: "1223580020001#cy_ridge_hs_main",
    addressQuery: "7900 N ELDRIDGE PKWY",
    fallbackYearBuilt: 2002,
  });
  assert.equal(ok, true);
  assert.equal(inspected.length, 1);
  assert.equal(inspected[0].props.building_id, "1223580020001#cy_ridge_hs_main");
  assert.equal(inspected[0].props.building_name, "Cypress Ridge High School");
  assert.deepEqual(inspected[0].geom, cyPoly);

  // Non-curated school_* click preserves fallbackBuildingName when PMTiles feature lacks building_name
  const okSchool = AtlasMapController.prototype.selectFeatureByIdOrHcad.call(fakeCtrl, {
    hcadNum: "0660460010001",
    featureId: "0660460010001#b1",
    addressQuery: "5100 MAPLE ST",
    fallbackYearBuilt: 1955,
    fallbackBuildingName: "Bellaire High School",
  });
  assert.equal(okSchool, true);
  assert.equal(inspected[1].props.building_name, "Bellaire High School");
});

test("UT-3: URL hash selection helper preserves #<slug> sub-building IDs while keeping #hcad= for single-building parcels", () => {
  const prevWin = globalThis.window;
  const prevDoc = globalThis.document;
  let pushedUrl = "";
  globalThis.document = { getElementById: () => null };
  globalThis.window = {
    location: { pathname: "/index.html", search: "", hash: "" },
    history: {
      replaceState: (_st, _title, url) => {
        pushedUrl = url;
      },
      pushState: (_st, _title, url) => {
        pushedUrl = url;
      },
    },
  };

  try {
    const store = createFilterStore();
    const fakeApp = {
      _suppressUrlUpdate: false,
      _isRestoringHistory: false,
      _lastHistorySignature: null,
      _lastHandledUrlKey: "",
      _activeChapterId: "",
      _activePortfolio: null,
      _activeTour: null,
      filterStore: store,
      _computeHistorySignature: HoustonAtlasApp.prototype._computeHistorySignature,
      _getSelectionUrlIds: HoustonAtlasApp.prototype._getSelectionUrlIds,
      mapController: {
        getCurrentViewport: () => ({ lat: 29.88821, lng: -95.60348, zoom: 17.4, pitch: 0 }),
        selectedFeatureProps: {
          id: "1223580020001#eldridge_trans_main",
          building_id: "1223580020001#eldridge_trans_main",
          hcad_num: "1223580020001",
        },
        selectedBoundaryFeature: null,
      },
    };

    HoustonAtlasApp.prototype._updateUrlHash.call(fakeApp, store.getState());
    assert.match(
      pushedUrl,
      /id=1223580020001%23eldridge_trans_main/,
      `Expected multi-building #<slug> selection to serialize id=1223580020001%23eldridge_trans_main, got ${pushedUrl}`
    );

    // Single-building parcel (#b1 default suffix) continues serializing hcad=
    fakeApp.mapController.selectedFeatureProps = {
      id: "0011470000001#b1",
      building_id: "0011470000001#b1",
      hcad_num: "0011470000001",
    };
    HoustonAtlasApp.prototype._updateUrlHash.call(fakeApp, store.getState());
    assert.match(
      pushedUrl,
      /hcad=0011470000001/,
      `Expected single-building #b1 selection to serialize hcad=0011470000001, got ${pushedUrl}`
    );

    // Multi-school parcel (#b1 on 0834040000022 Long Academy / 1183720000001 Clear Horizons ECHS) preserves #b1 via id=
    fakeApp._multiEntityHcads = new Set(["0834040000022", "1183720000001"]);
    fakeApp.mapController.selectedFeatureProps = {
      id: "0834040000022#b1",
      building_id: "0834040000022#b1",
      hcad_num: "0834040000022",
    };
    HoustonAtlasApp.prototype._updateUrlHash.call(fakeApp, store.getState());
    assert.match(
      pushedUrl,
      /id=0834040000022%23b1/,
      `Expected multi-school #b1 selection to serialize id=0834040000022%23b1, got ${pushedUrl}`
    );
  } finally {
    globalThis.window = prevWin;
    globalThis.document = prevDoc;
  }
});

test("UT-3: normalizeComparableAddress and Inspector Copy/Print share URLs preserve #<slug> sub-buildings", async () => {
  assert.equal(
    normalizeComparableAddress("7900 North Eldridge Parkway."),
    "7900 N ELDRIDGE PKWY"
  );
  assert.equal(
    normalizeComparableAddress("7600 N. Eldridge Pky"),
    "7600 N ELDRIDGE PKWY"
  );

  const prevWin = globalThis.window;
  const prevDoc = globalThis.document;
  const bodyEl = { innerHTML: "" };
  const modalEl = { classList: { remove: () => {} } };
  globalThis.document = {
    getElementById: (id) => {
      if (id === "dossier-modal") return modalEl;
      if (id === "dossier-modal-body") return bodyEl;
      return null;
    },
  };
  globalThis.window = {
    location: {
      origin: "https://davemorris-gcp.github.io",
      pathname: "/houston-building-atlas/index.html",
      search: "",
      hash: "",
    },
  };

  try {
    const store = createFilterStore();
    const subProps = {
      id: "0451440030163#impact_echs_main",
      building_id: "0451440030163#impact_echs_main",
      hcad_num: "0451440030163",
      building_name: "IMPACT Early College High School",
      address: "1415 MARKET ST",
      year_built: 1981,
      decade: 1980,
      use_category: "Civic / Institutional",
    };

    const fakeApp = {
      filterStore: store,
      _multiEntityHcads: new Set(["0451440030163"]),
      _activePhotoState: { photos: [{ url: "https://example.org/test.jpg", source: "Test" }] },
      _getSelectionUrlIds: HoustonAtlasApp.prototype._getSelectionUrlIds,
      _buildQrMatrixSvg: HoustonAtlasApp.prototype._buildQrMatrixSvg,
      _escapeResearchHtml: (s) => String(s || ""),
      _resolveBuildingNameAndAliases: () => ({
        primaryName: "IMPACT Early College High School",
        altNames: [],
      }),
      _resolveStyleAndClassInfo: () => ({ displayStyle: "Educational Campus" }),
      _resolveSanbornCoverageAtPoint: () => ({ summaryText: "Outside 1907-1951 Sanborn plates" }),
      _parseAddressForArchivalSearch: () => ({ cleanQuery: "1415 MARKET ST" }),
      _buildCityDirectoryResearchData: () => ({ auditHit: null, isAuditedAbsent: false }),
      mapController: {
        getCurrentViewport: () => ({ lat: 29.7361, lng: -94.9742, zoom: 17.5, pitch: 0 }),
      },
    };

    // 1. Direct shareable URL helper used by #btn-copy-share-link
    const { selectedHcad, selectedFeatureId } = fakeApp._getSelectionUrlIds(subProps);
    const copiedUrl = buildShareableUrl(
      store.getState(),
      fakeApp.mapController.getCurrentViewport(),
      {
        includeViewport: true,
        selectedHcad,
        selectedFeatureId,
        useQueryString: true,
      }
    );
    assert.match(
      copiedUrl,
      /[?&]id=0451440030163%23impact_echs_main/,
      `Expected #btn-copy-share-link URL to serialize id=0451440030163%23impact_echs_main, got ${copiedUrl}`
    );
    assert.doesNotMatch(copiedUrl, /[?&]hcad=0451440030163/);

    // 2. openPropertyDossierModal (Print Property Sheet) embeds ?id=0451440030163%23impact_echs_main rather than parcel-level ?hcad=
    await HoustonAtlasApp.prototype.openPropertyDossierModal.call(fakeApp, subProps);
    assert.match(
      bodyEl.innerHTML,
      /id=0451440030163%23impact_echs_main/,
      "Expected Print Property Sheet permalink to preserve 0451440030163%23impact_echs_main"
    );
    assert.doesNotMatch(bodyEl.innerHTML, /[?&]hcad=0451440030163/);
  } finally {
    globalThis.window = prevWin;
    globalThis.document = prevDoc;
  }
});
