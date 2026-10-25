/**
 * MapLibre GL JS + PMTiles controller for The Houston Building Atlas v2,
 * with automatic high-FPS HTML5 2D Canvas Vector + Slippy Tile fallback
 * for environments where WebGL context creation is unavailable.
 */

import {
  buildColorExpression,
  getYearColorHex,
  HISTORICAL_MAP_CATALOG,
  PRESERVATION_STATUS_ITEMS,
  SANBORN_MATERIAL_ITEMS,
  USE_CATEGORY_ITEMS,
} from "./palettes.js?v=20261024a";
import {
  buildAnnexationFilterExpression,
  buildFeatureFilterExpression,
  buildHahcFilterExpression,
  buildHistoricWardFilterExpression,
  buildHolcZonesFilterExpression,
  buildLandmarksFilterExpression,
  buildLostHoustonFilterExpression,
  buildRedBookFilterExpression,
  featureMatchesFilter,
  resolveActiveAnnexationDecade,
  resolveActiveWardEra,
} from "./filterStore.js?v=20261025f";
import {
  applyOverrideToProperties,
  loadCuratedOverrides,
} from "./curatedEdits.js?v=20261012b";
import { fetchHcadLiveRecord } from "./hcadLink.js?v=20261012b";

const BASEMAP_TILES = {
  dark_archival: {
    maxZoom: 16,
    tiles: [
      "https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}",
    ],
    labelTiles: [
      "https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}",
    ],
    attribution: "Tiles &copy; Esri &mdash; Esri, HERE, Garmin, OpenStreetMap contributors",
  },
  warm_parchment: {
    maxZoom: 16,
    tiles: [
      "https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
    ],
    labelTiles: [
      "https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}",
    ],
    attribution: "Tiles &copy; Esri &mdash; Esri, HERE, Garmin, OpenStreetMap contributors",
  },
  satellite: {
    maxZoom: 18,
    tiles: [
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    ],
    labelTiles: [
      "https://services.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
    ],
    attribution: "Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics",
  },
  historic_topo: {
    maxZoom: 15,
    tiles: [
      "https://services.arcgisonline.com/ArcGIS/rest/services/USA_Topo_Maps/MapServer/tile/{z}/{y}/{x}",
    ],
    attribution: "Historic USGS 7.5-Minute Topographic Quads &copy; USGS / National Geographic / Esri",
  },
};

function getHistoricalMapEntry(historicMapId) {
  const id = String(historicMapId || "sanborn_1907").trim();
  return (
    HISTORICAL_MAP_CATALOG.find((m) => m.id === id) ||
    HISTORICAL_MAP_CATALOG[0]
  );
}

function formatHistoricalTileUrl(tpl, z, tx, ty) {
  if (!tpl) return "";
  if (tpl.includes("{bbox-epsg-3857}")) {
    const ORIGIN_SHIFT = 20037508.342789244;
    const res = (2 * ORIGIN_SHIFT) / Math.pow(2, z);
    const minX = -ORIGIN_SHIFT + tx * res;
    const maxX = -ORIGIN_SHIFT + (tx + 1) * res;
    const maxY = ORIGIN_SHIFT - ty * res;
    const minY = ORIGIN_SHIFT - (ty + 1) * res;
    return tpl.replace(
      "{bbox-epsg-3857}",
      `${minX.toFixed(2)},${minY.toFixed(2)},${maxX.toFixed(2)},${maxY.toFixed(2)}`
    );
  }
  return tpl
    .replace("{z}", z)
    .replace("{x}", tx)
    .replace("{y}", ty);
}

function getSanbornMaterialColors(materialKey) {
  const found = SANBORN_MATERIAL_ITEMS.find((m) => m.key === materialKey);
  return (
    found || {
      key: "brick",
      color: "#F472B6",
      fillColor: "rgba(244, 114, 182, 0.28)",
      strokeColor: "#F472B6",
    }
  );
}

function evaluateFeatureColor(props, colorMode, paletteStyle) {
  if (!props) return "#524E4A";
  if (colorMode === "preservation_status") {
    if (props.protection_tier === "plm" || props.landmark_type === "Protected Landmark") {
      return "#E63946";
    }
    if (
      props.protection_tier === "coh_hd_contributing" ||
      props.contributing === "Contributing"
    ) {
      return "#2A9D8F";
    }
    if (props.protection_tier === "lm_waiver" || props.landmark_type === "Landmark") {
      return "#F59E0B";
    }
    if (props.protection_tier === "nrhp_eligible") {
      return "#C084FC";
    }
    if (
      props.protection_tier === "coh_hd_noncontributing" ||
      props.contributing === "Non-Contributing"
    ) {
      return "#64748B";
    }
    const yr = Number(props.year_built) || 0;
    if (yr >= 1836 && yr <= 1945) {
      return "#FB7185";
    }
    return "#475569";
  }
  if (colorMode === "use_category") {
    const found = USE_CATEGORY_ITEMS.find((i) => i.key === props.use_category);
    return found ? found.color : "#524E4A";
  }
  return getYearColorHex(props.year_built, paletteStyle);
}

function canCreateWebGLContext() {
  try {
    const canvas = document.createElement("canvas");
    const gl =
      canvas.getContext("webgl2") ||
      canvas.getContext("webgl") ||
      canvas.getContext("experimental-webgl");
    return Boolean(gl);
  } catch {
    return false;
  }
}

export class AtlasMapController {
  constructor({
    containerId,
    filterStore,
    onSelectFeature,
    onViewportStats,
    onPitchChange,
    onSelectTourStop,
    onSelectPortfolioWork,
    onOverlapStackChange,
    onIsolationChange,
  }) {
    this.containerId = containerId;
    this.filterStore = filterStore;
    this.onSelectFeature = onSelectFeature;
    this.onViewportStats = onViewportStats;
    this.onPitchChange = onPitchChange || null;
    this.onSelectTourStop = onSelectTourStop || null;
    this.onSelectPortfolioWork = onSelectPortfolioWork || null;
    this.onOverlapStackChange = onOverlapStackChange || null;
    this.onIsolationChange = onIsolationChange || null;
    this.map = null;
    this.popup = null;
    this.useCanvasFallback = false;
    this.canvasState = null;
    this.buildingsData = [];
    this.parcelsData = [];
    this.overlaysData = null;
    this.permitAlertsData = null;
    this.permitAlertsFC = { type: "FeatureCollection", features: [] };
    this.permitAlertsByHcad = new Map();
    this.permitAlertsByBldId = new Map();
    this.platWatchData = null;
    this.platWatchFC = { type: "FeatureCollection", features: [] };
    this.platWatchByHcad = new Map();
    this.hahcTrackerData = null;
    this.hahcById = new Map();
    this.hahcFilingsByHcad = new Map();
    this.hahcDistrictPrecedents = {};
    this.hahcDistrictPrecedentsNorm = new Map();
    this.landmarksByHcad = new Map();
    this.landmarksByBldId = new Map();
    this.nrhpPropertiesByHcad = new Map();
    this.nrhpPropertiesByBldId = new Map();
    this.curatedOverrides = {};
    this.sheetSyncStatus = null;
    this.overridesFC = { type: "FeatureCollection", features: [] };
    this.selectedFeatureId = null;
    this.selectedBoundaryFeature = null;
    this.isolatedBoundary = null;
    this._boundarySpatialIndex = null;
    this.overlapStack = [];
    this.overlapStackIndex = 0;
    this._lastOverlapClickPoint = null;
    this.pmtilesManifest = null;
    this.buildingFillLayerIds = ["buildings-fill"];
    this.buildingLineLayerIds = ["buildings-line"];
    this.buildingExtrusionLayerIds = ["buildings-extrusion"];
    this.highlightLayerIds = ["selected-feature-highlight"];
    this.activeTour = null;
    this.activeTourStopIndex = -1;
    this.tourFocusMode = "all";
    this.tourStopMarkers = [];
    this.portfolioWorks = [];
    this.activePortfolioWorkIndex = -1;
    this.portfolioMarkers = [];
    this.isReady = false;
  }

  async init(initialViewport = null) {
    const state = this.filterStore.getState();
    const center = initialViewport
      ? [initialViewport.lng, initialViewport.lat]
      : [-95.3805, 29.7662];
    const zoom = initialViewport ? initialViewport.zoom : 15.2;
    const pitch = initialViewport
      ? initialViewport.pitch
      : state.extrude3D
      ? 48
      : 0;

    await this._fetchDataPayloads();

    if (canCreateWebGLContext() && window.maplibregl) {
      try {
        await this._initMapLibre(center, zoom, pitch, state);
      } catch (err) {
        console.info("Switching to HTML5 2D Canvas cartographic engine:", err?.message || err);
        this._initCanvas2DEngine(center, zoom, pitch);
      }
    } else {
      this._initCanvas2DEngine(center, zoom, pitch);
    }

    this.isReady = true;
    const currentState = this.filterStore.getState();
    if (currentState.isolatedBoundary) {
      this._resolveIsolatedBoundaryFromSpec(currentState.isolatedBoundary, {
        flyTo: !initialViewport,
      });
    }
    this.syncWithState(currentState);
    this.computeViewportHistogram();

    this.filterStore.subscribe((nextState) => {
      this._syncIsolatedBoundaryWithState(nextState);
      this.syncWithState(nextState);
      this.computeViewportHistogram();
    });
  }

  async _fetchDataPayloads() {
    const [
      buildingsRes,
      parcelsRes,
      overlaysRes,
      manifestRes,
      overridesResult,
      permitAlertsRes,
      platWatchRes,
      hahcTrackerRes,
    ] = await Promise.all([
      fetch("public/data/buildings.geojson?v=20261024a"),
      fetch("public/data/parcels.geojson?v=20261011c"),
      fetch("public/data/overlays.json?v=20261025f"),
      fetch("public/data/pmtiles_manifest.json?v=20261011c").catch(() => null),
      loadCuratedOverrides(),
      fetch("public/data/active_permit_alerts.json?v=20261023g").catch(() => null),
      fetch("public/data/plat_agenda_monitor.json?v=20261023g").catch(() => null),
      fetch("public/data/hahc_coa_tracker.json?v=20261025f").catch(() => null),
    ]);

    const buildingsFC = await buildingsRes.json();
    const parcelsFC = await parcelsRes.json();
    const overlays = await overlaysRes.json();
    if (manifestRes && manifestRes.ok) {
      try {
        this.pmtilesManifest = await manifestRes.json();
      } catch {
        this.pmtilesManifest = null;
      }
    }

    let permitAlertsJson = null;
    if (permitAlertsRes && permitAlertsRes.ok) {
      try {
        permitAlertsJson = await permitAlertsRes.json();
      } catch {
        permitAlertsJson = null;
      }
    }
    this._indexPermitAlerts(permitAlertsJson);

    let platWatchJson = null;
    if (platWatchRes && platWatchRes.ok) {
      try {
        platWatchJson = await platWatchRes.json();
      } catch {
        platWatchJson = null;
      }
    }
    this._indexPlatWatch(platWatchJson);

    let hahcTrackerJson = null;
    if (hahcTrackerRes && hahcTrackerRes.ok) {
      try {
        hahcTrackerJson = await hahcTrackerRes.json();
      } catch {
        hahcTrackerJson = null;
      }
    }

    this.curatedOverrides = (overridesResult && overridesResult.overrides) || {};
    this.sheetSyncStatus = (overridesResult && overridesResult.syncStatus) || null;

    this.buildingsFC = buildingsFC;
    this.parcelsFC = parcelsFC;
    this.buildingsData = buildingsFC.features || [];
    this.parcelsData = parcelsFC.features || [];
    this.overlaysData = overlays;
    this._indexPreservationOverlays(overlays);
    this._indexHahcTracker(hahcTrackerJson, overlays);
    this._buildBoundarySpatialIndex();

    this._applyCuratedOverridesInMemory();
  }

  _indexPreservationOverlays(overlays) {
    this.landmarksByHcad = new Map();
    this.landmarksByBldId = new Map();
    this.nrhpPropertiesByHcad = new Map();
    this.nrhpPropertiesByBldId = new Map();
    this.redBookById = new Map();
    this.redBookByHcad = new Map();
    this.redBookByBldId = new Map();

    for (const feat of overlays?.landmarks?.features || []) {
      const p = feat?.properties;
      if (!p) continue;
      const hcad = String(p.hcad_num || "").trim();
      const bldId = String(p.building_id || "").trim();
      if (hcad) {
        const prev = this.landmarksByHcad.get(hcad);
        // Prefer Protected Landmark if multiple landmark records share an HCAD parcel
        if (!prev || p.is_protected_landmark) {
          this.landmarksByHcad.set(hcad, p);
        }
      }
      if (bldId) {
        const prevB = this.landmarksByBldId.get(bldId);
        if (!prevB || p.is_protected_landmark) {
          this.landmarksByBldId.set(bldId, p);
        }
      }
    }

    for (const feat of overlays?.nrhp_properties?.features || []) {
      const p = feat?.properties;
      if (!p || p.is_demolished_nrhp) continue;
      const hcad = String(p.hcad_num || "").trim();
      const bldId = String(p.building_id || "").trim();
      if (hcad && !this.nrhpPropertiesByHcad.has(hcad)) {
        this.nrhpPropertiesByHcad.set(hcad, p);
      }
      if (bldId && !this.nrhpPropertiesByBldId.has(bldId)) {
        this.nrhpPropertiesByBldId.set(bldId, p);
      }
    }

    const pushRb = (mapObj, key, propsObj) => {
      if (!key) return;
      const arr = mapObj.get(key) || [];
      arr.push(propsObj);
      mapObj.set(key, arr);
    };
    for (const feat of overlays?.red_book_1915?.features || []) {
      const p = feat?.properties;
      if (!p) continue;
      const rbId = String(p.id || "").trim();
      if (rbId) {
        this.redBookById.set(rbId, feat);
      }
      const hcad = String(p.hcad_num || "").trim();
      const bldId = String(p.building_id || "").trim();
      if (hcad) pushRb(this.redBookByHcad, hcad, p);
      if (bldId) pushRb(this.redBookByBldId, bldId, p);
    }
    const sortRbList = (arr) =>
      arr.sort((a, b) => {
        const sa = a.survival_status === "surviving_era_structure" ? 0 : 1;
        const sb = b.survival_status === "surviving_era_structure" ? 0 : 1;
        return sa - sb;
      });
    for (const arr of this.redBookByHcad.values()) sortRbList(arr);
    for (const arr of this.redBookByBldId.values()) sortRbList(arr);
  }

  getRedBookFeatureById(rbId) {
    const clean = String(rbId || "").trim();
    if (!clean) return null;
    return this.redBookById?.get(clean) || null;
  }

  getRedBookEntriesForBuilding({ hcadNum = "", buildingId = "" } = {}) {
    const out = [];
    const seen = new Set();
    const addAll = (list) => {
      if (!Array.isArray(list)) return;
      for (const item of list) {
        const k = String(item?.id || item?.name || "");
        if (!k || seen.has(k)) continue;
        seen.add(k);
        out.push(item);
      }
    };
    const cleanBld = String(buildingId || "").trim();
    const cleanHcad = String(hcadNum || "").trim();
    if (cleanBld && this.redBookByBldId?.has(cleanBld)) {
      addAll(this.redBookByBldId.get(cleanBld));
    }
    if (cleanHcad && this.redBookByHcad?.has(cleanHcad)) {
      addAll(this.redBookByHcad.get(cleanHcad));
    }
    if (cleanBld.includes("#")) {
      const prefixHcad = cleanBld.split("#")[0].trim();
      if (prefixHcad && this.redBookByHcad?.has(prefixHcad)) {
        addAll(this.redBookByHcad.get(prefixHcad));
      }
    }
    return out;
  }

  getLandmarkForBuilding({ hcadNum = "", buildingId = "" } = {}) {
    const cleanBld = String(buildingId || "").trim();
    if (cleanBld && this.landmarksByBldId?.has(cleanBld)) {
      return this.landmarksByBldId.get(cleanBld);
    }
    const cleanHcad = String(hcadNum || "").trim();
    if (cleanHcad && this.landmarksByHcad?.has(cleanHcad)) {
      return this.landmarksByHcad.get(cleanHcad);
    }
    if (cleanBld.includes("#")) {
      const prefixHcad = cleanBld.split("#")[0].trim();
      if (prefixHcad && this.landmarksByHcad?.has(prefixHcad)) {
        return this.landmarksByHcad.get(prefixHcad);
      }
    }
    return null;
  }

  getNrhpPropertyForBuilding({ hcadNum = "", buildingId = "" } = {}) {
    const cleanBld = String(buildingId || "").trim();
    if (cleanBld && this.nrhpPropertiesByBldId?.has(cleanBld)) {
      return this.nrhpPropertiesByBldId.get(cleanBld);
    }
    const cleanHcad = String(hcadNum || "").trim();
    if (cleanHcad && this.nrhpPropertiesByHcad?.has(cleanHcad)) {
      return this.nrhpPropertiesByHcad.get(cleanHcad);
    }
    if (cleanBld.includes("#")) {
      const prefixHcad = cleanBld.split("#")[0].trim();
      if (prefixHcad && this.nrhpPropertiesByHcad?.has(prefixHcad)) {
        return this.nrhpPropertiesByHcad.get(prefixHcad);
      }
    }
    return null;
  }

  _indexPermitAlerts(permitAlertsJson) {
    this.permitAlertsData = permitAlertsJson || {
      updated_at: null,
      lookback_days: 45,
      summary: {
        total_flagged_buildings: 0,
        critical_count: 0,
        watch_count: 0,
        coh_permits_matched: 0,
        dshs_notifications_matched: 0,
      },
      alerts: [],
    };
    this.permitAlertsByHcad = new Map();
    this.permitAlertsByBldId = new Map();
    const pointFeatures = [];

    for (const alert of this.permitAlertsData.alerts || []) {
      if (!alert) continue;
      const primaryHcad = String(alert.hcad_num || "").trim();
      const primaryBldId = String(alert.building_id || "").trim();
      if (primaryHcad) {
        this.permitAlertsByHcad.set(primaryHcad, alert);
      }
      if (primaryBldId) {
        this.permitAlertsByBldId.set(primaryBldId, alert);
      }
      if (Array.isArray(alert.related_hcads)) {
        for (const rh of alert.related_hcads) {
          const cleanRh = String(rh || "").trim();
          if (cleanRh && !this.permitAlertsByHcad.has(cleanRh)) {
            this.permitAlertsByHcad.set(cleanRh, alert);
          }
        }
      }
      if (Array.isArray(alert.related_building_ids)) {
        for (const rb of alert.related_building_ids) {
          const cleanRb = String(rb || "").trim();
          if (cleanRb && !this.permitAlertsByBldId.has(cleanRb)) {
            this.permitAlertsByBldId.set(cleanRb, alert);
          }
        }
      }

      const lng = Number(alert.lng);
      const lat = Number(alert.lat);
      if (Number.isFinite(lng) && Number.isFinite(lat)) {
        pointFeatures.push({
          type: "Feature",
          geometry: {
            type: "Point",
            coordinates: [lng, lat],
          },
          properties: {
            id: `permit_alert_${primaryHcad || primaryBldId || pointFeatures.length}`,
            hcad_num: primaryHcad,
            building_id: primaryBldId,
            title: alert.title || alert.address || "Historic Property",
            landmark_name: alert.landmark_name || "",
            address: alert.address || "",
            year_built: Number(alert.year_built) || 0,
            yr_remodel: Number(alert.yr_remodel) || 0,
            style: alert.style || "",
            architect: alert.architect || "",
            neighborhood: alert.neighborhood || "",
            historic_district: alert.historic_district || "",
            good_brick_summary: alert.good_brick_summary || "",
            owner: alert.owner || "",
            severity: alert.severity || "watch",
            severity_label: alert.severity_label || "Permit Watch",
            headline: alert.headline || "",
            latest_filing_date: alert.latest_filing_date || "",
            coh_permit_count: Array.isArray(alert.coh_permits) ? alert.coh_permits.length : 0,
            dshs_count: Array.isArray(alert.dshs_notifications)
              ? alert.dshs_notifications.length
              : 0,
          },
        });
      }
    }

    this.permitAlertsFC = {
      type: "FeatureCollection",
      features: pointFeatures,
    };
  }

  getPermitAlertsPayload() {
    return this.permitAlertsData;
  }

  getPermitAlertForBuilding({ hcadNum = "", buildingId = "" } = {}) {
    const cleanBld = String(buildingId || "").trim();
    if (cleanBld && this.permitAlertsByBldId?.has(cleanBld)) {
      return this.permitAlertsByBldId.get(cleanBld);
    }
    const cleanHcad = String(hcadNum || "").trim();
    if (cleanHcad && this.permitAlertsByHcad?.has(cleanHcad)) {
      return this.permitAlertsByHcad.get(cleanHcad);
    }
    if (cleanBld.includes("#")) {
      const prefixHcad = cleanBld.split("#")[0].trim();
      if (prefixHcad && this.permitAlertsByHcad?.has(prefixHcad)) {
        return this.permitAlertsByHcad.get(prefixHcad);
      }
    }
    return null;
  }

  _indexPlatWatch(platWatchJson) {
    this.platWatchData = platWatchJson && typeof platWatchJson === "object" ? platWatchJson : null;
    this.platWatchByHcad = new Map();
    const pointFeatures = [];
    const items = Array.isArray(this.platWatchData?.items) ? this.platWatchData.items : [];
    const reviews =
      this.platWatchData?.reviews && typeof this.platWatchData.reviews === "object"
        ? this.platWatchData.reviews
        : {};

    for (const item of items) {
      if (!item) continue;
      const appKey = String(item.app || item.appNo || item.id || "").trim();
      const rev = (appKey && reviews[appKey]) || {};
      const at = item.atlas || item.atlas_enrichment || {};
      const rawHcadStr = [rev.hcad || "", item.hcad || "", at.hcad_num || ""].join(" ");
      const hcadList = Array.from(
        new Set(
          rawHcadStr
            .split(/[,;\s]+/)
            .map((s) => s.trim())
            .filter((s) => /^\d{10,13}$/.test(s))
            .map((s) => s.padStart(13, "0"))
        )
      );
      if (appKey === "DPV-4801-NMAIN" && !hcadList.includes("0391500000001")) {
        hcadList.push("0391500000001");
      }
      if (appKey === "DPV-26073533" && !hcadList.includes("0012070000001")) {
        hcadList.push("0012070000001");
      }
      const primaryHcad = hcadList[0] || "";
      const lon = Number(at.lng ?? at.lon);
      const lat = Number(at.lat);
      const flags = Array.isArray(item._c?.flags) ? item._c.flags : [];
      const rawTier = String(rev.tier || item.tier || "").trim();
      const tier =
        rawTier === "1" || rawTier === "tier1"
          ? "tier1"
          : rawTier === "2" || rawTier === "tier2"
          ? "tier2"
          : rawTier === "3" || rawTier === "tier3"
          ? "tier3"
          : "none";
      const isVariance = Boolean(
        item.is_variance || flags.includes("variance") || flags.includes("dpv")
      );
      const isPublicHearing = Boolean(item.is_public_hearing || flags.includes("hearing"));
      const isCritical = tier === "tier1" || isVariance || isPublicHearing;
      const addrStr = String(
        rev.address || at.primaryAddress || at.matched_address || item.noticeLocation || item.name || ""
      )
        .split(/[;,]/)[0]
        .trim();
      const yrBuilt =
        Number(at.earliestYearBuilt || at.year_built || rev.yearBuilt) || null;

      const normItem = {
        ...item,
        id: appKey || primaryHcad,
        appNo: appKey,
        tier,
        is_variance: isVariance,
        is_public_hearing: isPublicHearing,
        variance_summary: item.varianceSummary || item.variance_summary || "",
        notes: rev.why || item.notes || "",
        hcad_num: primaryHcad,
        related_hcads: hcadList,
        address: addrStr,
        building_id: at.buildingId || "",
        building_name: at.buildingName || item.name || "",
        year_built: yrBuilt,
        subdivision: at.subdivisionMatch || item.prevSub || "",
        neighborhood: at.superNeighborhoodMatch || item.superN || "",
        lng: Number.isFinite(lon) ? lon : null,
        lat: Number.isFinite(lat) ? lat : null,
      };

      for (const hid of hcadList) {
        if (hid && !this.platWatchByHcad.has(hid)) {
          this.platWatchByHcad.set(hid, normItem);
        }
      }

      if (Number.isFinite(lon) && Number.isFinite(lat)) {
        pointFeatures.push({
          type: "Feature",
          geometry: {
            type: "Point",
            coordinates: [lon, lat],
          },
          properties: {
            id: normItem.id,
            plat_item_id: normItem.id,
            app_no: normItem.appNo,
            subdivision_name: item.name || "",
            plat_type: item.type || "",
            plat_section: item.section || "I",
            plat_bucket: item._c?.group || item.bucket || "check",
            plat_tier: tier,
            is_variance: isVariance,
            is_public_hearing: isPublicHearing,
            variance_summary: normItem.variance_summary,
            public_hearing_date: item.pcDateIso || item.public_hearing_date || "",
            developer: item.developer || "",
            applicant_company: item.org || item.company || "",
            hcad_num: primaryHcad,
            address: addrStr,
            building_name: normItem.building_name,
            year_built: yrBuilt,
            historic_district: item.hd || at.historic_district || "",
            subdivision: normItem.subdivision,
            subdivision_earliest_year: Number(at.subdivisionEarliestYear || at.subdivision_earliest_year) || null,
            notes: normItem.notes,
            severity: isCritical ? "critical" : "watch",
            lng: lon,
            lat: lat,
          },
        });
      }
    }

    this.platWatchFC = {
      type: "FeatureCollection",
      features: pointFeatures,
    };
  }

  getPlatWatchPayload() {
    return this.platWatchData;
  }

  getPlatWatchFeatureCount() {
    return Array.isArray(this.platWatchFC?.features) ? this.platWatchFC.features.length : 0;
  }

  getPlatWatchForBuilding({ hcadNum = "", buildingId = "" } = {}) {
    const cleanHcad = String(hcadNum || "").trim();
    if (cleanHcad && this.platWatchByHcad?.has(cleanHcad)) {
      return this.platWatchByHcad.get(cleanHcad);
    }
    const cleanBld = String(buildingId || "").trim();
    if (cleanBld.includes("#")) {
      const prefixHcad = cleanBld.split("#")[0].trim();
      if (prefixHcad && this.platWatchByHcad?.has(prefixHcad)) {
        return this.platWatchByHcad.get(prefixHcad);
      }
    }
    return null;
  }

  _normalizeHahcDistrictKey(rawName) {
    return String(rawName || "")
      .trim()
      .toLowerCase()
      .replace(/\s+historic\s+district$/i, "")
      .replace(/\s+hd$/i, "")
      .replace(/[^a-z0-9]+/g, " ")
      .trim();
  }

  _indexHahcTracker(hahcJson, overlays) {
    this.hahcTrackerData = hahcJson && typeof hahcJson === "object" ? hahcJson : null;
    this.hahcById = new Map();
    this.hahcFilingsByHcad = new Map();
    this.hahcDistrictPrecedents =
      this.hahcTrackerData?.district_precedents &&
      typeof this.hahcTrackerData.district_precedents === "object"
        ? this.hahcTrackerData.district_precedents
        : {};
    this.hahcDistrictPrecedentsNorm = new Map();
    for (const [distName, summary] of Object.entries(this.hahcDistrictPrecedents)) {
      const norm = this._normalizeHahcDistrictKey(distName);
      if (norm) {
        this.hahcDistrictPrecedentsNorm.set(norm, summary);
      }
    }

    for (const feat of overlays?.hahc_coa_filings?.features || []) {
      const fid = String(feat?.properties?.id || "").trim();
      if (fid) {
        this.hahcById.set(fid, feat);
      }
      const hcad = String(feat?.properties?.hcad_num || "").trim();
      if (hcad && !this.hahcById.has(hcad)) {
        this.hahcById.set(hcad, feat);
      }
    }

    const filings = Array.isArray(this.hahcTrackerData?.filings)
      ? this.hahcTrackerData.filings
      : [];
    for (const f of filings) {
      const hcad = String(f?.hcad_num || "").trim();
      if (!hcad) continue;
      if (!this.hahcFilingsByHcad.has(hcad)) {
        this.hahcFilingsByHcad.set(hcad, []);
      }
      this.hahcFilingsByHcad.get(hcad).push(f);
    }
    for (const list of this.hahcFilingsByHcad.values()) {
      list.sort((a, b) =>
        String(b.meeting_date || "").localeCompare(String(a.meeting_date || ""))
      );
    }
  }

  getHahcTrackerPayload() {
    return this.hahcTrackerData;
  }

  getHahcFeatureCount() {
    return Array.isArray(this.overlaysData?.hahc_coa_filings?.features)
      ? this.overlaysData.hahc_coa_filings.features.length
      : 0;
  }

  getHahcFeatureById(id) {
    const clean = String(id || "").trim();
    if (!clean) return null;
    return this.hahcById?.get(clean) || null;
  }

  getHahcFilingsForBuilding({ hcadNum = "", buildingId = "" } = {}) {
    const cleanHcad = String(hcadNum || "").trim();
    if (cleanHcad && this.hahcFilingsByHcad?.has(cleanHcad)) {
      return this.hahcFilingsByHcad.get(cleanHcad);
    }
    const cleanBld = String(buildingId || "").trim();
    if (cleanBld.includes("#")) {
      const prefixHcad = cleanBld.split("#")[0].trim();
      if (prefixHcad && this.hahcFilingsByHcad?.has(prefixHcad)) {
        return this.hahcFilingsByHcad.get(prefixHcad);
      }
    }
    return [];
  }

  getHahcDistrictPrecedents(historicDistrictName = "") {
    const raw = String(historicDistrictName || "").trim();
    if (!raw) return null;
    if (this.hahcDistrictPrecedents && this.hahcDistrictPrecedents[raw]) {
      return this.hahcDistrictPrecedents[raw];
    }
    const norm = this._normalizeHahcDistrictKey(raw);
    if (norm && this.hahcDistrictPrecedentsNorm?.has(norm)) {
      return this.hahcDistrictPrecedentsNorm.get(norm);
    }
    return null;
  }

  _applyCuratedOverridesInMemory() {
    const ovMap = this.curatedOverrides || {};
    const overrideFeaturesByKey = new Map();

    // Collect HCAD numbers and building IDs that are suppressed without replacement geometry
    const suppressedHcads = new Set();
    const suppressedIds = new Set();
    for (const [ovKey, ov] of Object.entries(ovMap)) {
      if (!ov) continue;
      if (ov.suppress_only) {
        suppressedIds.add(ovKey);
        const hcad = String(ov.hcad_num || ovKey.split("#")[0] || "").trim();
        if (hcad) suppressedHcads.add(hcad);
      }
      if (Array.isArray(ov.suppress_shard_hcads)) {
        for (const sh of ov.suppress_shard_hcads) {
          const cleanSh = String(sh || "").trim();
          if (cleanSh && (!ovMap[cleanSh] || ovMap[cleanSh].suppress_only)) {
            suppressedHcads.add(cleanSh);
          }
        }
      }
    }

    if (suppressedHcads.size > 0 || suppressedIds.size > 0) {
      this.buildingsData = this.buildingsData.filter((feat) => {
        const featId = String(feat.properties?.id || "").trim();
        const bldId = String(feat.properties?.building_id || "").trim();
        const hcad = String(feat.properties?.hcad_num || "").trim();
        if (featId && suppressedIds.has(featId)) return false;
        if (bldId && suppressedIds.has(bldId)) return false;
        if (hcad && suppressedHcads.has(hcad)) return false;
        return true;
      });
      if (this.buildingsFC) {
        this.buildingsFC.features = this.buildingsData;
      }
    }

    const putOverrideFeature = (baseKey, geom, props) => {
      if (!geom) return;
      if (geom.type === "MultiPolygon" && Array.isArray(geom.coordinates)) {
        for (let idx = 0; idx < geom.coordinates.length; idx++) {
          const polyCoords = geom.coordinates[idx];
          if (!Array.isArray(polyCoords) || !polyCoords.length) continue;
          const subKey = idx === 0 ? baseKey : `${baseKey}#fp_${idx}`;
          overrideFeaturesByKey.set(subKey, {
            type: "Feature",
            geometry: {
              type: "Polygon",
              coordinates: polyCoords,
            },
            properties: {
              ...props,
              id: subKey,
              building_id: idx === 0 ? props.building_id || baseKey : subKey,
            },
          });
        }
      } else {
        overrideFeaturesByKey.set(baseKey, {
          type: "Feature",
          geometry: geom,
          properties: { ...props },
        });
      }
    };

    for (const feat of this.buildingsData) {
      const featId = String(feat.properties?.id || "").trim();
      const hcad = String(feat.properties?.hcad_num || "").trim();
      const matchKey =
        featId && ovMap[featId]
          ? featId
          : hcad && ovMap[hcad] && !ovMap[hcad].is_building_override
          ? hcad
          : "";
      if (matchKey) {
        if (ovMap[matchKey].suppress_only) continue;
        feat.properties = applyOverrideToProperties(feat.properties, ovMap);
        putOverrideFeature(matchKey, ovMap[matchKey].geometry || feat.geometry, feat.properties);
      }
    }

    for (const feat of this.parcelsData) {
      const hcad = String(feat.properties?.hcad_num || "").trim();
      if (hcad && ovMap[hcad] && !ovMap[hcad].is_building_override && !ovMap[hcad].suppress_only) {
        feat.properties = applyOverrideToProperties(feat.properties, ovMap);
      }
    }

    for (const [ovKey, ov] of Object.entries(ovMap)) {
      if (!ov || ov.keep_shard_footprints || ov.suppress_only) continue;
      if (!overrideFeaturesByKey.has(ovKey) && ov.geometry) {
        const rawPrefix = String(ovKey.split("#")[0] || "").trim();
        const hcadNum = String(
          ov.hcad_num || (/^\d+$/.test(rawPrefix) ? rawPrefix : "")
        ).trim();
        const baseProps = applyOverrideToProperties(
          {
            id: ov.id || ovKey,
            building_id: ov.building_id || ov.id || ovKey,
            hcad_num: hcadNum,
            address: ov.address || "",
            landmark_name: ov.landmark_name || "",
            year_built: ov.year_built || 0,
            decade: ov.decade || 0,
            stories: Number(ov.stories) || 1,
            height_m: Number(ov.height_m) || 4.5,
            use_category: ov.use_category || "Residential",
            historic_district: ov.historic_district || "",
            contributing: ov.contributing || "Contributing",
            footprint_source: "observed",
            is_building_override: Boolean(ov.is_building_override),
            replace_parcel_shards: Boolean(ov.replace_parcel_shards),
            keep_shard_footprints: Boolean(ov.keep_shard_footprints),
          },
          ovMap
        );

        if (ov.geometry.type === "MultiPolygon" && Array.isArray(ov.geometry.coordinates)) {
          for (let idx = 0; idx < ov.geometry.coordinates.length; idx++) {
            const polyCoords = ov.geometry.coordinates[idx];
            if (!Array.isArray(polyCoords) || !polyCoords.length) continue;
            const subKey = idx === 0 ? ovKey : `${ovKey}#fp_${idx}`;
            overrideFeaturesByKey.set(subKey, {
              type: "Feature",
              geometry: {
                type: "Polygon",
                coordinates: polyCoords,
              },
              properties: {
                ...baseProps,
                id: subKey,
                building_id: idx === 0 ? baseProps.building_id : subKey,
              },
            });
          }
        } else {
          overrideFeaturesByKey.set(ovKey, {
            type: "Feature",
            geometry: ov.geometry,
            properties: baseProps,
          });
        }
      }
    }

    this.overridesFC = {
      type: "FeatureCollection",
      features: Array.from(overrideFeaturesByKey.values()),
    };
  }

  async reloadCuratedOverrides(customSheetCsvUrl = null) {
    const res = await loadCuratedOverrides(customSheetCsvUrl);
    this.curatedOverrides = res.overrides || {};
    this.sheetSyncStatus = res.syncStatus || null;
    this._refreshCuratedOverridesSource();
    return this.sheetSyncStatus;
  }

  _refreshCuratedOverridesSource() {
    this._applyCuratedOverridesInMemory();
    this._captureDynamicOverrideGeometries();
    if (this.map && this.map.getSource("curated-overrides-src")) {
      this.map.getSource("curated-overrides-src").setData(this.overridesFC);
    }
    this.syncWithState(this.filterStore.getState());
    this.computeViewportHistogram();
  }

  _applyCurrentFilterStateToMap() {
    this.syncWithState(this.filterStore.getState());
    this.computeViewportHistogram();
  }

  /* ========================================================================
     WebGL Mode (MapLibre GL JS + PMTiles)
     ======================================================================== */
  async _initMapLibre(center, zoom, pitch, state) {
    const maplibregl = window.maplibregl;
    const pmtilesLib = window.pmtiles;

    if (pmtilesLib && pmtilesLib.Protocol) {
      const protocol = new pmtilesLib.Protocol();
      maplibregl.addProtocol("pmtiles", protocol.tile);
    }

    this.map = new maplibregl.Map({
      container: this.containerId,
      center,
      zoom,
      pitch,
      bearing: 0,
      maxPitch: 65,
      minZoom: 9.5,
      maxZoom: 19.5,
      preserveDrawingBuffer: true,
      attributionControl: false,
      style: {
        version: 8,
        glyphs: "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf",
        sources: {
          "basemap-dark": {
            type: "raster",
            tiles: BASEMAP_TILES.dark_archival.tiles,
            tileSize: 256,
            maxzoom: 16,
            attribution: BASEMAP_TILES.dark_archival.attribution,
          },
          "basemap-dark-labels": {
            type: "raster",
            tiles: BASEMAP_TILES.dark_archival.labelTiles,
            tileSize: 256,
            maxzoom: 16,
          },
          "basemap-light": {
            type: "raster",
            tiles: BASEMAP_TILES.warm_parchment.tiles,
            tileSize: 256,
            maxzoom: 16,
            attribution: BASEMAP_TILES.warm_parchment.attribution,
          },
          "basemap-light-labels": {
            type: "raster",
            tiles: BASEMAP_TILES.warm_parchment.labelTiles,
            tileSize: 256,
            maxzoom: 16,
          },
          "basemap-satellite": {
            type: "raster",
            tiles: BASEMAP_TILES.satellite.tiles,
            tileSize: 256,
            maxzoom: 18,
            attribution: BASEMAP_TILES.satellite.attribution,
          },
          "basemap-satellite-labels": {
            type: "raster",
            tiles: BASEMAP_TILES.satellite.labelTiles,
            tileSize: 256,
            maxzoom: 16,
          },
          "basemap-historic-topo": {
            type: "raster",
            tiles: [getHistoricalMapEntry(state.historicMapId).tileUrl],
            tileSize: 256,
            maxzoom: getHistoricalMapEntry(state.historicMapId).maxZoom || 19,
            attribution:
              getHistoricalMapEntry(state.historicMapId).attribution ||
              BASEMAP_TILES.historic_topo.attribution,
          },
          "openfreemap-vector": {
            type: "vector",
            url: "https://tiles.openfreemap.org/planet",
          },
        },
        layers: [
          {
            id: "basemap-solid-bg",
            type: "background",
            paint: {
              "background-color":
                state.basemap === "solid_light"
                  ? "#F4F1EA"
                  : state.basemap === "warm_parchment"
                  ? "#EBE6DC"
                  : "#0D1117",
            },
          },
          {
            id: "basemap-dark-layer",
            type: "raster",
            source: "basemap-dark",
            layout: {
              visibility: state.basemap === "dark_archival" ? "visible" : "none",
            },
          },
          {
            id: "basemap-light-layer",
            type: "raster",
            source: "basemap-light",
            layout: {
              visibility: state.basemap === "warm_parchment" ? "visible" : "none",
            },
          },
          {
            id: "basemap-satellite-layer",
            type: "raster",
            source: "basemap-satellite",
            layout: {
              visibility: state.basemap === "satellite" ? "visible" : "none",
            },
          },
          {
            id: "basemap-historic-topo-layer",
            type: "raster",
            source: "basemap-historic-topo",
            layout: {
              visibility:
                state.layers?.historicMap && !state.historicMapSpyglass
                  ? "visible"
                  : "none",
            },
            paint: {
              "raster-opacity": Math.max(
                0.1,
                Math.min(1, (Number(state.historicMapOpacity) || 82) / 100)
              ),
              "raster-contrast": 0.08,
            },
          },
          {
            id: "vector-roads-highzoom",
            type: "line",
            source: "openfreemap-vector",
            "source-layer": "transportation",
            minzoom: 15,
            layout: {
              visibility:
                state.basemap === "solid_dark" || state.basemap === "solid_light"
                  ? "none"
                  : "visible",
            },
            paint: {
              "line-color": "rgba(148, 163, 184, 0.28)",
              "line-width": [
                "interpolate",
                ["linear"],
                ["zoom"],
                15,
                1.5,
                19,
                8.0,
              ],
            },
          },
          {
            id: "basemap-dark-labels-layer",
            type: "raster",
            source: "basemap-dark-labels",
            layout: {
              visibility: state.basemap === "dark_archival" ? "visible" : "none",
            },
          },
          {
            id: "basemap-light-labels-layer",
            type: "raster",
            source: "basemap-light-labels",
            layout: {
              visibility: state.basemap === "warm_parchment" ? "visible" : "none",
            },
          },
          {
            id: "basemap-satellite-labels-layer",
            type: "raster",
            source: "basemap-satellite-labels",
            layout: {
              visibility: state.basemap === "satellite" ? "visible" : "none",
            },
          },
        ],
      },
    });
    this._activeWebGLHistoricMapId = getHistoricalMapEntry(state.historicMapId).id;

    this.map.addControl(
      new maplibregl.NavigationControl({ visualizePitch: true }),
      "bottom-right"
    );
    this.map.addControl(
      new maplibregl.ScaleControl({ maxWidth: 110, unit: "imperial" }),
      "bottom-right"
    );

    const compassBtn = this.map.getContainer().querySelector(".maplibregl-ctrl-compass");
    if (compassBtn) {
      const updateCompassTitle = () => {
        const currentPitch = Math.round(this.map.getPitch());
        const label =
          currentPitch < 5
            ? "Tilt map to 3D perspective (50°)"
            : `Reset 3D tilt (${currentPitch}°) to flat 2D North`;
        compassBtn.setAttribute("title", label);
        compassBtn.setAttribute("aria-label", label);
      };
      updateCompassTitle();
      this.map.on("pitch", updateCompassTitle);

      compassBtn.addEventListener(
        "click",
        (e) => {
          e.preventDefault();
          e.stopImmediatePropagation();
          const currentPitch = this.map.getPitch();
          if (currentPitch < 5 && Math.abs(this.map.getBearing()) < 5) {
            this.filterStore.setState({ extrude3D: true });
            this.setCameraPitch(50, -12);
          } else {
            this.filterStore.setState({ extrude3D: false });
            this.setCameraPitch(0, 0);
          }
        },
        { capture: true }
      );
    }

    this.popup = new maplibregl.Popup({
      closeButton: false,
      closeOnClick: false,
      offset: 12,
      className: "atlas-hover-popup",
    });

    await new Promise((resolve, reject) => {
      this.map.once("load", resolve);
      this.map.once("error", (e) => {
        if (e && e.error && String(e.error.message || "").includes("WebGL")) {
          reject(e.error);
        }
      });
    });

    this._addMapLibreSourcesAndLayers();
    this._bindMapLibreInteractions();
    this._initSpyglassOverlay();
  }

  _buildLabelPointsFeatureCollection(polyFC) {
    const features = [];
    for (const feat of polyFC?.features || []) {
      const p = feat.properties || {};
      const lng = Number(p.label_lng);
      const lat = Number(p.label_lat);
      if (!Number.isFinite(lng) || !Number.isFinite(lat)) continue;
      features.push({
        type: "Feature",
        geometry: { type: "Point", coordinates: [lng, lat] },
        properties: { ...p },
      });
    }
    return { type: "FeatureCollection", features };
  }

  _addMapLibreSourcesAndLayers() {
    const overlays = this.overlaysData;
    const cacheBust =
      (this.pmtilesManifest && this.pmtilesManifest.cache_bust) || "20261009a";
    const pmtilesUrl = new URL(
      `public/data/houston_atlas.pmtiles?v=${encodeURIComponent(cacheBust)}`,
      window.location.href
    ).href;
    try {
      this.map.addSource("atlas-pmtiles", {
        type: "vector",
        url: `pmtiles://${pmtilesUrl}`,
      });
    } catch (err) {
      console.warn("PMTiles source registration warning:", err);
    }

    const shardFiles =
      this.pmtilesManifest && Array.isArray(this.pmtilesManifest.building_shards)
        ? this.pmtilesManifest.building_shards
        : [];

    for (let i = 0; i < shardFiles.length; i++) {
      const shardUrl = new URL(
        `public/data/${shardFiles[i]}?v=${encodeURIComponent(cacheBust)}`,
        window.location.href
      ).href;
      this.map.addSource(`atlas-shard-${i}`, {
        type: "vector",
        url: `pmtiles://${shardUrl}`,
      });
    }

    this.map.addSource("buildings-src", { type: "geojson", data: this.buildingsFC });
    this.map.addSource("curated-overrides-src", { type: "geojson", data: this.overridesFC });
    this.map.addSource("parcels-src", { type: "geojson", data: this.parcelsFC });
    this.map.addSource("annexations-src", {
      type: "geojson",
      tolerance: 0.05,
      data: overlays.annexations || { type: "FeatureCollection", features: [] },
    });
    this.map.addSource("historic-wards-src", {
      type: "geojson",
      data: overlays.historic_wards || { type: "FeatureCollection", features: [] },
    });
    this.map.addSource("historic-wards-labels-src", {
      type: "geojson",
      data: this._buildLabelPointsFeatureCollection(overlays.historic_wards),
    });
    this.map.addSource("super-neighborhoods-src", {
      type: "geojson",
      data: overlays.super_neighborhoods || { type: "FeatureCollection", features: [] },
    });
    this.map.addSource("super-neighborhoods-labels-src", {
      type: "geojson",
      data: this._buildLabelPointsFeatureCollection(overlays.super_neighborhoods),
    });
    this.map.addSource("neighborhoods-src", {
      type: "geojson",
      data: overlays.neighborhoods || { type: "FeatureCollection", features: [] },
    });
    this.map.addSource("neighborhoods-labels-src", {
      type: "geojson",
      data: this._buildLabelPointsFeatureCollection(overlays.neighborhoods),
    });
    this.map.addSource("platted-subdivisions-src", {
      type: "geojson",
      data: overlays.platted_subdivisions || { type: "FeatureCollection", features: [] },
    });
    this.map.addSource("platted-subdivisions-labels-src", {
      type: "geojson",
      data: this._buildLabelPointsFeatureCollection(overlays.platted_subdivisions),
    });
    this.map.addSource("land-use-protections-src", {
      type: "geojson",
      data: overlays.land_use_protections || { type: "FeatureCollection", features: [] },
    });
    this.map.addSource("land-use-protections-labels-src", {
      type: "geojson",
      data: this._buildLabelPointsFeatureCollection(overlays.land_use_protections),
    });
    this.map.addSource("selected-boundary-src", {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
    });
    this.map.addSource("historic-districts-src", {
      type: "geojson",
      data: overlays.historic_districts || { type: "FeatureCollection", features: [] },
    });
    this.map.addSource("heritage-districts-src", {
      type: "geojson",
      data: overlays.heritage_districts || { type: "FeatureCollection", features: [] },
    });
    this.map.addSource("nrhp-districts-src", {
      type: "geojson",
      data: overlays.nrhp_districts || { type: "FeatureCollection", features: [] },
    });
    this.map.addSource("potential-historic-districts-src", {
      type: "geojson",
      data: overlays.potential_historic_districts || { type: "FeatureCollection", features: [] },
    });
    this.map.addSource("potential-historic-districts-labels-src", {
      type: "geojson",
      data: this._buildLabelPointsFeatureCollection(overlays.potential_historic_districts),
    });
    this.map.addSource("cultural-districts-src", {
      type: "geojson",
      data: overlays.cultural_districts || { type: "FeatureCollection", features: [] },
    });
    this.map.addSource("cultural-districts-labels-src", {
      type: "geojson",
      data: this._buildLabelPointsFeatureCollection(overlays.cultural_districts),
    });
    this.map.addSource("holc-1937-zones-src", {
      type: "geojson",
      data: overlays.holc_1937_zones || { type: "FeatureCollection", features: [] },
    });
    this.map.addSource("holc-1937-zones-labels-src", {
      type: "geojson",
      data: this._buildLabelPointsFeatureCollection(overlays.holc_1937_zones),
    });
    this.map.addSource("red-book-1915-src", {
      type: "geojson",
      data: overlays.red_book_1915 || { type: "FeatureCollection", features: [] },
    });
    this.map.addSource("nrhp-properties-src", {
      type: "geojson",
      data: overlays.nrhp_properties || { type: "FeatureCollection", features: [] },
    });
    const armorFeatures = [
      ...(overlays.land_use_protections?.features || []),
      ...(overlays.platted_subdivisions?.features || []).filter((f) =>
        Boolean(f?.properties?.has_deed_docs)
      ),
    ];
    this.map.addSource("protection-armor-zones-src", {
      type: "geojson",
      data: { type: "FeatureCollection", features: armorFeatures },
    });
    this.map.addSource("thc-markers-src", {
      type: "geojson",
      data: overlays.thc_markers || { type: "FeatureCollection", features: [] },
    });
    this.map.addSource("landmarks-src", {
      type: "geojson",
      data: overlays.landmarks || { type: "FeatureCollection", features: [] },
    });
    this.map.addSource("good-brick-src", {
      type: "geojson",
      data: overlays.good_brick_awards || { type: "FeatureCollection", features: [] },
    });
    this.map.addSource("historical-waterways-src", {
      type: "geojson",
      data: overlays.historical_waterways || { type: "FeatureCollection", features: [] },
    });
    this.map.addSource("historical-railroads-src", {
      type: "geojson",
      data: overlays.historical_railroads || { type: "FeatureCollection", features: [] },
    });
    this.map.addSource("lost-houston-src", {
      type: "geojson",
      data: overlays.lost_houston || { type: "FeatureCollection", features: [] },
    });
    this.map.addSource("lost-houston-labels-src", {
      type: "geojson",
      data: this._buildLabelPointsFeatureCollection(overlays.lost_houston),
    });
    this.map.addSource("relocated-structures-src", {
      type: "geojson",
      data: overlays.relocated_structures || { type: "FeatureCollection", features: [] },
    });
    this.map.addSource("permit-watch-src", {
      type: "geojson",
      data: this.permitAlertsFC || { type: "FeatureCollection", features: [] },
    });
    this.map.addSource("plat-watch-src", {
      type: "geojson",
      data: this.platWatchFC || { type: "FeatureCollection", features: [] },
    });
    this.map.addSource("hahc-coa-src", {
      type: "geojson",
      data: overlays.hahc_coa_filings || { type: "FeatureCollection", features: [] },
    });
    this.map.addSource("portfolio-works-src", {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
    });

    const state = this.filterStore.getState();
    const colorExpr = buildColorExpression(state.colorMode, state.paletteStyle);
    const filterExpr = buildFeatureFilterExpression(state);
    const shardFilterExpr = this._buildShardLayerFilter(filterExpr);
    const wardFilterExpr = buildHistoricWardFilterExpression(state);
    const annexFilterExpr = buildAnnexationFilterExpression(state);
    const lostHoustonFilterExpr = buildLostHoustonFilterExpression(state);
    const holcFilterExpr = buildHolcZonesFilterExpression(state);
    const redBookFilterExpr = buildRedBookFilterExpression(state, null);
    const hahcFilterExpr = buildHahcFilterExpression(state, null);

    this.map.addLayer({
      id: "annexations-fill",
      type: "fill",
      source: "annexations-src",
      filter: annexFilterExpr,
      paint: {
        "fill-color": [
          "case",
          ["==", ["get", "annex_subtype"], "spoke_or_spa"],
          "#EA580C",
          "#D97706",
        ],
        "fill-opacity": [
          "case",
          ["==", ["get", "annex_subtype"], "spoke_or_spa"],
          0.14,
          0.09,
        ],
      },
    });
    this.map.addLayer({
      id: "annexations-line",
      type: "line",
      source: "annexations-src",
      filter: annexFilterExpr,
      paint: {
        "line-color": [
          "case",
          ["==", ["get", "annex_subtype"], "spoke_or_spa"],
          "#FB923C",
          "#F59E0B",
        ],
        "line-width": [
          "case",
          ["==", ["get", "annex_subtype"], "spoke_or_spa"],
          1.15,
          2.0,
        ],
        "line-dasharray": [4, 3],
        "line-opacity": [
          "case",
          ["==", ["get", "annex_subtype"], "spoke_or_spa"],
          0.82,
          0.9,
        ],
      },
    });
    this.map.addLayer({
      id: "historic-wards-fill",
      type: "fill",
      source: "historic-wards-src",
      filter: wardFilterExpr,
      paint: {
        "fill-color": ["coalesce", ["get", "color"], "#FB923C"],
        "fill-opacity": 0.12,
      },
    });
    this.map.addLayer({
      id: "historic-wards-line",
      type: "line",
      source: "historic-wards-src",
      filter: wardFilterExpr,
      paint: {
        "line-color": ["coalesce", ["get", "color"], "#FB923C"],
        "line-width": 2.4,
        "line-opacity": 0.9,
      },
    });
    this.map.addLayer({
      id: "holc-1937-zones-fill",
      type: "fill",
      source: "holc-1937-zones-src",
      ...(holcFilterExpr ? { filter: holcFilterExpr } : {}),
      layout: { visibility: state.layers?.holc1937 ? "visible" : "none" },
      paint: {
        "fill-color": [
          "match",
          ["get", "holc_grade"],
          "A",
          "#22C55E",
          "B",
          "#38BDF8",
          "C",
          "#FACC15",
          "D",
          "#EF4444",
          "#94A3B8",
        ],
        "fill-opacity": 0.17,
      },
    });
    this.map.addLayer({
      id: "holc-1937-zones-line",
      type: "line",
      source: "holc-1937-zones-src",
      ...(holcFilterExpr ? { filter: holcFilterExpr } : {}),
      layout: { visibility: state.layers?.holc1937 ? "visible" : "none" },
      paint: {
        "line-color": [
          "match",
          ["get", "holc_grade"],
          "A",
          "#4ADE80",
          "B",
          "#7DD3FC",
          "C",
          "#FDE047",
          "D",
          "#F87171",
          "#CBD5E1",
        ],
        "line-width": 2.3,
        "line-opacity": 0.92,
      },
    });
    this.map.addLayer({
      id: "super-neighborhoods-fill",
      type: "fill",
      source: "super-neighborhoods-src",
      paint: {
        "fill-color": "#60A5FA",
        "fill-opacity": 0.06,
      },
    });
    this.map.addLayer({
      id: "super-neighborhoods-line",
      type: "line",
      source: "super-neighborhoods-src",
      paint: {
        "line-color": "#60A5FA",
        "line-width": 1.8,
        "line-dasharray": [3, 2],
        "line-opacity": 0.82,
      },
    });
    this.map.addLayer({
      id: "neighborhoods-fill",
      type: "fill",
      source: "neighborhoods-src",
      paint: {
        "fill-color": "#95C959",
        "fill-opacity": 0.07,
      },
    });
    this.map.addLayer({
      id: "neighborhoods-line",
      type: "line",
      source: "neighborhoods-src",
      paint: {
        "line-color": "#95C959",
        "line-width": 1.4,
        "line-opacity": 0.78,
      },
    });
    this.map.addLayer({
      id: "platted-subdivisions-fill",
      type: "fill",
      source: "platted-subdivisions-src",
      paint: {
        "fill-color": [
          "case",
          ["boolean", ["get", "has_deed_docs"], false],
          "#F59E0B",
          "#22D3EE",
        ],
        "fill-opacity": [
          "case",
          ["boolean", ["get", "has_deed_docs"], false],
          0.15,
          0.08,
        ],
      },
    });
    this.map.addLayer({
      id: "platted-subdivisions-line",
      type: "line",
      source: "platted-subdivisions-src",
      paint: {
        "line-color": [
          "case",
          ["boolean", ["get", "has_deed_docs"], false],
          "#FBBF24",
          "#22D3EE",
        ],
        "line-width": [
          "case",
          ["boolean", ["get", "has_deed_docs"], false],
          2.4,
          1.45,
        ],
        "line-dasharray": [3, 2],
        "line-opacity": 0.9,
      },
    });
    this.map.addLayer({
      id: "land-use-protections-fill",
      type: "fill",
      source: "land-use-protections-src",
      paint: {
        "fill-color": [
          "match",
          ["get", "protection_type"],
          "smbl",
          "#10B981",
          "conservation",
          "#F43F5E",
          "#F59E0B",
        ],
        "fill-opacity": 0.14,
      },
    });
    this.map.addLayer({
      id: "land-use-protections-line",
      type: "line",
      source: "land-use-protections-src",
      paint: {
        "line-color": [
          "match",
          ["get", "protection_type"],
          "smbl",
          "#34D399",
          "conservation",
          "#FB7185",
          "#FBBF24",
        ],
        "line-width": 1.8,
        "line-dasharray": [2, 1.5],
        "line-opacity": 0.9,
      },
    });
    this.map.addLayer({
      id: "selected-boundary-fill",
      type: "fill",
      source: "selected-boundary-src",
      filter: [
        "any",
        ["==", ["geometry-type"], "Polygon"],
        ["==", ["geometry-type"], "MultiPolygon"],
      ],
      paint: {
        "fill-color": "#FDE047",
        "fill-opacity": 0.11,
      },
    });
    this.map.addLayer({
      id: "selected-boundary-line",
      type: "line",
      source: "selected-boundary-src",
      filter: ["!=", ["geometry-type"], "Point"],
      paint: {
        "line-color": "#FDE047",
        "line-width": 3.5,
        "line-dasharray": [2, 1.5],
        "line-opacity": 0.98,
      },
    });
    this.map.addLayer({
      id: "selected-boundary-point",
      type: "circle",
      source: "selected-boundary-src",
      filter: ["==", ["geometry-type"], "Point"],
      paint: {
        "circle-radius": 9.5,
        "circle-color": "#FDE047",
        "circle-stroke-color": "#0F172A",
        "circle-stroke-width": 2.4,
        "circle-opacity": 0.95,
      },
    });
    this.map.addLayer({
      id: "cultural-districts-fill",
      type: "fill",
      source: "cultural-districts-src",
      layout: { visibility: state.layers?.culturalDistricts ? "visible" : "none" },
      paint: { "fill-color": "#EC4899", "fill-opacity": 0.06 },
    });
    this.map.addLayer({
      id: "cultural-districts-line",
      type: "line",
      source: "cultural-districts-src",
      layout: { visibility: state.layers?.culturalDistricts ? "visible" : "none" },
      paint: {
        "line-color": "#F472B6",
        "line-width": 2.0,
        "line-dasharray": [4, 2],
        "line-opacity": 0.88,
      },
    });
    this.map.addLayer({
      id: "potential-historic-districts-fill",
      type: "fill",
      source: "potential-historic-districts-src",
      layout: { visibility: state.layers?.potentialHistoricDistricts ? "visible" : "none" },
      paint: {
        "fill-color": [
          "case",
          ["boolean", ["get", "already_designated_coh_hd"], false],
          "#38BDF8",
          "#FB923C",
        ],
        "fill-opacity": 0.09,
      },
    });
    this.map.addLayer({
      id: "potential-historic-districts-line",
      type: "line",
      source: "potential-historic-districts-src",
      layout: { visibility: state.layers?.potentialHistoricDistricts ? "visible" : "none" },
      paint: {
        "line-color": [
          "case",
          ["boolean", ["get", "already_designated_coh_hd"], false],
          "#38BDF8",
          "#FB923C",
        ],
        "line-width": 2.1,
        "line-dasharray": [5, 2, 1.5, 2],
        "line-opacity": 0.92,
      },
    });
    this.map.addLayer({
      id: "protection-armor-zones-fill",
      type: "fill",
      source: "protection-armor-zones-src",
      layout: {
        visibility: state.colorMode === "preservation_status" ? "visible" : "none",
      },
      paint: {
        "fill-color": "#38BDF8",
        "fill-opacity": 0.13,
      },
    });
    this.map.addLayer({
      id: "protection-armor-zones-line",
      type: "line",
      source: "protection-armor-zones-src",
      layout: {
        visibility: state.colorMode === "preservation_status" ? "visible" : "none",
      },
      paint: {
        "line-color": "#38BDF8",
        "line-width": 1.25,
        "line-dasharray": [2, 2],
        "line-opacity": 0.78,
      },
    });
    this.map.addLayer({
      id: "nrhp-districts-fill",
      type: "fill",
      source: "nrhp-districts-src",
      paint: { "fill-color": "#A855F7", "fill-opacity": 0.07 },
    });
    this.map.addLayer({
      id: "nrhp-districts-line",
      type: "line",
      source: "nrhp-districts-src",
      paint: { "line-color": "#C084FC", "line-width": 1.8, "line-dasharray": [3, 2] },
    });
    this.map.addLayer({
      id: "heritage-districts-fill",
      type: "fill",
      source: "heritage-districts-src",
      paint: { "fill-color": "#10B981", "fill-opacity": 0.09 },
    });
    this.map.addLayer({
      id: "heritage-districts-line",
      type: "line",
      source: "heritage-districts-src",
      paint: { "line-color": "#34D399", "line-width": 2.2 },
    });
    this.map.addLayer({
      id: "historic-districts-fill",
      type: "fill",
      source: "historic-districts-src",
      paint: { "fill-color": "#38BDF8", "fill-opacity": 0.06 },
    });
    this.map.addLayer({
      id: "historic-districts-line",
      type: "line",
      source: "historic-districts-src",
      paint: { "line-color": "#38BDF8", "line-width": 2.0, "line-opacity": 0.85 },
    });
    this.map.addLayer({
      id: "historical-waterways-casing",
      type: "line",
      source: "historical-waterways-src",
      layout: {
        "line-cap": "round",
        "line-join": "round",
      },
      paint: {
        "line-color": "rgba(9, 13, 22, 0.72)",
        "line-width": [
          "match",
          ["get", "waterway_type"],
          "bayou",
          5.2,
          "buried_gully",
          4.4,
          "historic_oxbow",
          4.4,
          3.8,
        ],
        "line-opacity": 0.75,
      },
    });
    this.map.addLayer({
      id: "historical-waterways-line-solid",
      type: "line",
      source: "historical-waterways-src",
      filter: [
        "!",
        ["in", ["get", "waterway_type"], ["literal", ["buried_gully", "historic_oxbow"]]],
      ],
      layout: {
        "line-cap": "round",
        "line-join": "round",
      },
      paint: {
        "line-color": [
          "match",
          ["get", "waterway_type"],
          "bayou",
          "#0EA5E9",
          "creek",
          "#22D3EE",
          "#38BDF8",
        ],
        "line-width": ["match", ["get", "waterway_type"], "bayou", 3.4, 2.3],
        "line-opacity": 0.96,
      },
    });
    this.map.addLayer({
      id: "historical-waterways-line-dashed",
      type: "line",
      source: "historical-waterways-src",
      filter: [
        "in",
        ["get", "waterway_type"],
        ["literal", ["buried_gully", "historic_oxbow"]],
      ],
      layout: {
        "line-cap": "round",
        "line-join": "round",
      },
      paint: {
        "line-color": [
          "match",
          ["get", "waterway_type"],
          "buried_gully",
          "#FBBF24",
          "historic_oxbow",
          "#A78BFA",
          "#FBBF24",
        ],
        "line-width": 2.9,
        "line-dasharray": [3, 2],
        "line-opacity": 0.96,
      },
    });
    this.map.addLayer({
      id: "historical-railroads-casing",
      type: "line",
      source: "historical-railroads-src",
      filter: ["!=", ["get", "rail_type"], "depot"],
      layout: {
        "line-cap": "round",
        "line-join": "round",
      },
      paint: {
        "line-color": "rgba(9, 13, 22, 0.75)",
        "line-width": [
          "match",
          ["get", "rail_type"],
          "mainline",
          4.8,
          "abandoned_trail",
          4.4,
          "streetcar_interurban",
          4.2,
          3.6,
        ],
        "line-opacity": 0.76,
      },
    });
    this.map.addLayer({
      id: "historical-railroads-line-solid",
      type: "line",
      source: "historical-railroads-src",
      filter: [
        "all",
        ["!=", ["get", "rail_type"], "depot"],
        ["!", ["in", ["get", "rail_type"], ["literal", ["abandoned_trail", "streetcar_interurban"]]]],
      ],
      layout: {
        "line-cap": "round",
        "line-join": "round",
      },
      paint: {
        "line-color": [
          "match",
          ["get", "rail_type"],
          "mainline",
          "#F59E0B",
          "#94A3B8",
        ],
        "line-width": ["match", ["get", "rail_type"], "mainline", 2.9, 2.0],
        "line-opacity": 0.96,
      },
    });
    this.map.addLayer({
      id: "historical-railroads-line-dashed",
      type: "line",
      source: "historical-railroads-src",
      filter: [
        "in",
        ["get", "rail_type"],
        ["literal", ["abandoned_trail", "streetcar_interurban"]],
      ],
      layout: {
        "line-cap": "round",
        "line-join": "round",
      },
      paint: {
        "line-color": [
          "match",
          ["get", "rail_type"],
          "abandoned_trail",
          "#FB7185",
          "streetcar_interurban",
          "#C084FC",
          "#FB7185",
        ],
        "line-width": 2.7,
        "line-dasharray": [3, 2],
        "line-opacity": 0.96,
      },
    });
    this.map.addLayer({
      id: "historical-railroads-ties",
      type: "line",
      source: "historical-railroads-src",
      filter: ["==", ["get", "rail_type"], "mainline"],
      paint: {
        "line-color": "#FEF3C7",
        "line-width": 4.2,
        "line-dasharray": [0.4, 2.6],
        "line-opacity": 0.72,
      },
    });
    this.map.addLayer({
      id: "parcels-fill",
      type: "fill",
      source: "parcels-src",
      filter: filterExpr,
      paint: { "fill-color": colorExpr, "fill-opacity": 0.78 },
    });
    this.map.addLayer({
      id: "parcels-line",
      type: "line",
      source: "parcels-src",
      filter: filterExpr,
      paint: { "line-color": "#94A3B8", "line-width": 0.6, "line-opacity": 0.42 },
    });

    this.buildingFillLayerIds = [];
    this.buildingLineLayerIds = [];
    this.buildingExtrusionLayerIds = [];
    this.highlightLayerIds = [];
    this.shardSourceIds = [];

    // Deterministic sub-centimeter height offset per year_built prevents WebGL depth-buffer Z-fighting on touching roofs
    const extrusionHeightExpr = [
      "+",
      ["to-number", ["get", "height_m"], 4.5],
      ["*", ["%", ["to-number", ["get", "year_built"], 1900], 97], 0.0003],
    ];

    if (shardFiles.length > 0) {
      for (let i = 0; i < shardFiles.length; i++) {
        const srcId = `atlas-shard-${i}`;
        const fillId = `buildings-fill-${i}`;
        const lineId = `buildings-line-${i}`;
        const extId = `buildings-extrusion-${i}`;
        const hlId = `selected-feature-highlight-${i}`;

        this.shardSourceIds.push(srcId);
        this.buildingFillLayerIds.push(fillId);
        this.buildingLineLayerIds.push(lineId);
        this.buildingExtrusionLayerIds.push(extId);
        this.highlightLayerIds.push(hlId);

        this.map.addLayer({
          id: fillId,
          type: "fill",
          source: srcId,
          "source-layer": "buildings",
          filter: shardFilterExpr,
          paint: { "fill-color": colorExpr, "fill-opacity": 0.9 },
        });
        this.map.addLayer({
          id: lineId,
          type: "line",
          source: srcId,
          "source-layer": "buildings",
          minzoom: 14,
          filter: shardFilterExpr,
          paint: { "line-color": "rgba(15, 17, 21, 0.65)", "line-width": 0.6 },
        });
        this.map.addLayer({
          id: hlId,
          type: "line",
          source: srcId,
          "source-layer": "buildings",
          filter: ["==", ["get", "id"], ""],
          layout: { visibility: state.extrude3D ? "none" : "visible" },
          paint: { "line-color": "#FDE047", "line-width": 3.2 },
        });
      }
      // Designated / District overlay footprints for Historic Status lens over PMTiles shards
      this.map.addLayer({
        id: "historic-status-buildings-fill",
        type: "fill",
        source: "buildings-src",
        filter: ["all", ["has", "protection_tier"], shardFilterExpr],
        layout: {
          visibility:
            !state.extrude3D && state.colorMode === "preservation_status" ? "visible" : "none",
        },
        paint: { "fill-color": colorExpr, "fill-opacity": 0.94 },
      });
    } else {
      this.buildingFillLayerIds = ["buildings-fill"];
      this.buildingLineLayerIds = ["buildings-line"];
      this.buildingExtrusionLayerIds = ["buildings-extrusion"];
      this.highlightLayerIds = ["selected-feature-highlight"];

      this.map.addLayer({
        id: "buildings-fill",
        type: "fill",
        source: "buildings-src",
        filter: shardFilterExpr,
        paint: { "fill-color": colorExpr, "fill-opacity": 0.9 },
      });
      this.map.addLayer({
        id: "buildings-line",
        type: "line",
        source: "buildings-src",
        filter: shardFilterExpr,
        paint: { "line-color": "rgba(15, 17, 21, 0.65)", "line-width": 0.6 },
      });
      this.map.addLayer({
        id: "selected-feature-highlight",
        type: "line",
        source: "buildings-src",
        filter: ["==", ["get", "id"], ""],
        layout: { visibility: state.extrude3D ? "none" : "visible" },
        paint: { "line-color": "#FDE047", "line-width": 3.2 },
      });
    }

    // Curated Overrides 2D Layers
    this.map.addLayer({
      id: "curated-overrides-fill",
      type: "fill",
      source: "curated-overrides-src",
      filter: filterExpr,
      paint: { "fill-color": colorExpr, "fill-opacity": 0.94 },
    });
    this.map.addLayer({
      id: "curated-overrides-line",
      type: "line",
      source: "curated-overrides-src",
      minzoom: 14,
      filter: filterExpr,
      paint: { "line-color": "rgba(149, 201, 89, 0.85)", "line-width": 1.1 },
    });
    this.map.addLayer({
      id: "curated-overrides-highlight",
      type: "line",
      source: "curated-overrides-src",
      filter: ["==", ["get", "id"], ""],
      layout: { visibility: state.extrude3D ? "none" : "visible" },
      paint: { "line-color": "#FDE047", "line-width": 3.4 },
    });
    this.highlightLayerIds.push("curated-overrides-highlight");

    // Guided Walking Tour Route Source & Ground-Plane Layers (added BEFORE 3D fill-extrusion
    // layers so street routes and building entrance connectors pass behind 3D extrusions)
    this.map.addSource("tour-route-src", {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
    });
    this.map.addLayer({
      id: "tour-connector-casing",
      type: "line",
      source: "tour-route-src",
      filter: ["==", ["get", "feature_type"], "connector"],
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": "rgba(15, 17, 21, 0.78)",
        "line-width": 3.6,
      },
    });
    this.map.addLayer({
      id: "tour-connector-line",
      type: "line",
      source: "tour-route-src",
      filter: ["==", ["get", "feature_type"], "connector"],
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": "#95C959",
        "line-width": 1.9,
        "line-dasharray": [1.2, 1.6],
        "line-opacity": 0.9,
      },
    });
    this.map.addLayer({
      id: "tour-route-casing",
      type: "line",
      source: "tour-route-src",
      filter: ["==", ["get", "feature_type"], "route"],
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": "rgba(15, 17, 21, 0.9)",
        "line-width": 6.0,
      },
    });
    this.map.addLayer({
      id: "tour-route-line",
      type: "line",
      source: "tour-route-src",
      filter: ["==", ["get", "feature_type"], "route"],
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": "#FDE047",
        "line-width": 3.1,
        "line-dasharray": [2.2, 1.8],
        "line-opacity": 0.98,
      },
    });

    // Dedicated single-feature 2D selection source so clicking a building on a multi-building
    // campus parcel (e.g. Rice University) highlights ONLY the clicked building polygon in 2D mode.
    this.map.addSource("selected-feature-src", {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
    });
    this.map.addLayer({
      id: "selected-feature-outline",
      type: "line",
      source: "selected-feature-src",
      layout: { visibility: state.extrude3D ? "none" : "visible" },
      paint: { "line-color": "#FDE047", "line-width": 3.5 },
    });

    // 3D Fill-Extrusion Layers (rendered above 2D ground lines so 3D volumes occlude ground paths)
    if (shardFiles.length > 0) {
      for (let i = 0; i < shardFiles.length; i++) {
        const srcId = `atlas-shard-${i}`;
        const extId = `buildings-extrusion-${i}`;
        this.map.addLayer({
          id: extId,
          type: "fill-extrusion",
          source: srcId,
          "source-layer": "buildings",
          filter: shardFilterExpr,
          layout: { visibility: state.extrude3D ? "visible" : "none" },
          paint: {
            "fill-extrusion-color": colorExpr,
            "fill-extrusion-height": extrusionHeightExpr,
            "fill-extrusion-base": 0,
            "fill-extrusion-opacity": 0.95,
          },
        });
      }
      this.map.addLayer({
        id: "historic-status-buildings-extrusion",
        type: "fill-extrusion",
        source: "buildings-src",
        filter: ["all", ["has", "protection_tier"], shardFilterExpr],
        layout: {
          visibility:
            state.extrude3D && state.colorMode === "preservation_status" ? "visible" : "none",
        },
        paint: {
          "fill-extrusion-color": colorExpr,
          "fill-extrusion-height": extrusionHeightExpr,
          "fill-extrusion-base": 0,
          "fill-extrusion-opacity": 0.96,
        },
      });
    } else {
      this.map.addLayer({
        id: "buildings-extrusion",
        type: "fill-extrusion",
        source: "buildings-src",
        filter: shardFilterExpr,
        layout: { visibility: state.extrude3D ? "visible" : "none" },
        paint: {
          "fill-extrusion-color": colorExpr,
          "fill-extrusion-height": extrusionHeightExpr,
          "fill-extrusion-base": 0,
          "fill-extrusion-opacity": 0.95,
        },
      });
    }

    this.map.addLayer({
      id: "curated-overrides-extrusion",
      type: "fill-extrusion",
      source: "curated-overrides-src",
      filter: filterExpr,
      layout: { visibility: state.extrude3D ? "visible" : "none" },
      paint: {
        "fill-extrusion-color": colorExpr,
        "fill-extrusion-height": extrusionHeightExpr,
        "fill-extrusion-base": 0,
        "fill-extrusion-opacity": 0.96,
      },
    });

    const sanbornMaterialColorExpr = [
      "match",
      ["coalesce", ["get", "sanborn_material"], "brick"],
      "wood_frame",
      "#FACC15",
      "brick",
      "#F472B6",
      "stone_concrete",
      "#60A5FA",
      "iron_steel",
      "#94A3B8",
      "fireproof",
      "#A3E635",
      "#F472B6",
    ];
    const showLostHouston =
      Boolean(state.layers?.lostHouston) || Boolean(state.historicalSnapshotMode);

    this.map.addLayer({
      id: "lost-houston-fill",
      type: "fill",
      source: "lost-houston-src",
      filter: lostHoustonFilterExpr,
      layout: { visibility: showLostHouston ? "visible" : "none" },
      paint: {
        "fill-color": sanbornMaterialColorExpr,
        "fill-opacity": 0.28,
      },
    });
    this.map.addLayer({
      id: "lost-houston-line",
      type: "line",
      source: "lost-houston-src",
      filter: lostHoustonFilterExpr,
      layout: { visibility: showLostHouston ? "visible" : "none" },
      paint: {
        "line-color": sanbornMaterialColorExpr,
        "line-width": 2.3,
        "line-dasharray": [2.2, 1.5],
        "line-opacity": 0.96,
      },
    });
    this.map.addLayer({
      id: "lost-houston-extrusion",
      type: "fill-extrusion",
      source: "lost-houston-src",
      filter: lostHoustonFilterExpr,
      layout: {
        visibility: showLostHouston && state.extrude3D ? "visible" : "none",
      },
      paint: {
        "fill-extrusion-color": sanbornMaterialColorExpr,
        "fill-extrusion-height": ["to-number", ["get", "height_m"], 10.5],
        "fill-extrusion-base": 0,
        "fill-extrusion-opacity": 0.38,
      },
    });

    // Depth-occluded 3D volumetric selection wireframe source & layer:
    // Renders the selected building's 3D roofline collar (z = H), ground-base collar (z = 0),
    // vertical corner edge ribs (z = 0..H), and nearby 3D occluder volumes in a single shared
    // WebGL depth buffer so back edges and occluded corners never X-ray through 3D structures.
    this.map.addSource("selected-feature-3d-src", {
      type: "geojson",
      tolerance: 0,
      maxzoom: 22,
      data: { type: "FeatureCollection", features: [] },
    });
    this.map.addLayer({
      id: "selected-feature-3d-extrusion",
      type: "fill-extrusion",
      source: "selected-feature-3d-src",
      layout: { visibility: state.extrude3D ? "visible" : "none" },
      paint: {
        "fill-extrusion-color": ["coalesce", ["get", "wire_color"], "#FDE047"],
        "fill-extrusion-height": ["to-number", ["get", "wire_height"], 4.5],
        "fill-extrusion-base": ["to-number", ["get", "wire_base"], 0],
        "fill-extrusion-opacity": 0.97,
      },
    });

    // Polygon Isolation Mask & Highlight Border Layers
    this.map.addSource("isolation-mask-src", {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
    });
    this.map.addSource("isolation-border-src", {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
    });
    this.map.addLayer({
      id: "isolation-mask-fill",
      type: "fill",
      source: "isolation-mask-src",
      layout: { visibility: "none" },
      paint: {
        "fill-color": "#0D1117",
        "fill-opacity": 0.92,
      },
    });
    this.map.addLayer({
      id: "isolation-border-fill",
      type: "fill",
      source: "isolation-border-src",
      layout: { visibility: "none" },
      paint: {
        "fill-color": "#95C959",
        "fill-opacity": 0.14,
      },
    });
    this.map.addLayer({
      id: "isolation-border-line",
      type: "line",
      source: "isolation-border-src",
      layout: { visibility: "none", "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": "#FDE047",
        "line-width": 3.2,
        "line-opacity": 0.98,
      },
    });

    this.map.addLayer({
      id: "thc-markers-circle",
      type: "circle",
      source: "thc-markers-src",
      paint: {
        "circle-radius": 5.0,
        "circle-color": "#C084FC",
        "circle-stroke-color": "#1E1B4B",
        "circle-stroke-width": 1.5,
      },
    });
    const initLmFilter = buildLandmarksFilterExpression(state);
    this.map.addLayer({
      id: "nrhp-properties-glow",
      type: "circle",
      source: "nrhp-properties-src",
      filter: ["!", ["boolean", ["get", "is_demolished_nrhp"], false]],
      layout: { visibility: state.layers?.nrhpProperties ? "visible" : "none" },
      paint: {
        "circle-radius": 9.8,
        "circle-color": "#A855F7",
        "circle-opacity": 0.30,
        "circle-blur": 0.45,
      },
    });
    this.map.addLayer({
      id: "nrhp-properties-circle",
      type: "circle",
      source: "nrhp-properties-src",
      layout: { visibility: state.layers?.nrhpProperties ? "visible" : "none" },
      paint: {
        "circle-radius": [
          "case",
          ["boolean", ["get", "is_demolished_nrhp"], false],
          4.8,
          5.8,
        ],
        "circle-color": [
          "case",
          ["boolean", ["get", "is_demolished_nrhp"], false],
          "#475569",
          "#A855F7",
        ],
        "circle-stroke-color": [
          "case",
          ["boolean", ["get", "is_demolished_nrhp"], false],
          "#CBD5E1",
          "#FAF5FF",
        ],
        "circle-stroke-width": [
          "case",
          ["boolean", ["get", "is_demolished_nrhp"], false],
          1.4,
          1.8,
        ],
      },
    });
    this.map.addLayer({
      id: "landmarks-waiver-ring",
      type: "circle",
      source: "landmarks-src",
      ...(initLmFilter ? { filter: initLmFilter } : {}),
      paint: {
        "circle-radius": [
          "case",
          ["==", ["get", "protection_tier"], "lm_waiver_vulnerable"],
          9.4,
          0,
        ],
        "circle-color": "rgba(15, 23, 42, 0.0)",
        "circle-stroke-color": "#FBBF24",
        "circle-stroke-width": [
          "case",
          ["==", ["get", "protection_tier"], "lm_waiver_vulnerable"],
          1.5,
          0,
        ],
        "circle-stroke-opacity": 0.88,
      },
    });
    this.map.addLayer({
      id: "landmarks-circle",
      type: "circle",
      source: "landmarks-src",
      ...(initLmFilter ? { filter: initLmFilter } : {}),
      paint: {
        "circle-radius": 6.0,
        "circle-color": [
          "case",
          ["==", ["get", "protection_tier"], "lm_waiver_vulnerable"],
          "#F59E0B",
          ["==", ["get", "designation"], "Protected Landmark"],
          "#E63946",
          "#E63946",
        ],
        "circle-stroke-color": [
          "case",
          ["==", ["get", "protection_tier"], "lm_in_hd"],
          "#2DD4BF",
          ["==", ["get", "protection_tier"], "lm_waiver_vulnerable"],
          "#0F172A",
          "#FFFBEB",
        ],
        "circle-stroke-width": [
          "case",
          ["==", ["get", "protection_tier"], "lm_in_hd"],
          2.1,
          1.7,
        ],
      },
    });
    this.map.addLayer({
      id: "good-brick-glow",
      type: "circle",
      source: "good-brick-src",
      paint: {
        "circle-radius": [
          "case",
          [">", ["to-number", ["get", "good_brick_count"], 1], 1],
          11.5,
          9.5,
        ],
        "circle-color": "#95C959",
        "circle-opacity": 0.32,
        "circle-blur": 0.45,
      },
    });
    this.map.addLayer({
      id: "good-brick-circle",
      type: "circle",
      source: "good-brick-src",
      paint: {
        "circle-radius": [
          "case",
          [">", ["to-number", ["get", "good_brick_count"], 1], 1],
          7.2,
          5.8,
        ],
        "circle-color": "#95C959",
        "circle-stroke-color": "#F4F9EE",
        "circle-stroke-width": 1.8,
      },
    });
    this.map.addLayer({
      id: "red-book-1915-glow",
      type: "circle",
      source: "red-book-1915-src",
      filter: redBookFilterExpr
        ? ["all", ["==", ["get", "survival_status"], "surviving_era_structure"], redBookFilterExpr]
        : ["==", ["get", "survival_status"], "surviving_era_structure"],
      layout: { visibility: state.layers?.redBook1915 ? "visible" : "none" },
      paint: {
        "circle-radius": 10.8,
        "circle-color": [
          "match",
          ["get", "category"],
          "church",
          "#F59E0B",
          "school",
          "#38BDF8",
          "business",
          "#FB7185",
          "residence",
          "#C084FC",
          "#F59E0B",
        ],
        "circle-opacity": 0.36,
        "circle-blur": 0.45,
      },
    });
    this.map.addLayer({
      id: "red-book-1915-circle",
      type: "circle",
      source: "red-book-1915-src",
      ...(redBookFilterExpr ? { filter: redBookFilterExpr } : {}),
      layout: { visibility: state.layers?.redBook1915 ? "visible" : "none" },
      paint: {
        "circle-radius": [
          "match",
          ["get", "survival_status"],
          "surviving_era_structure",
          6.5,
          "rebuilt_later_structure",
          5.4,
          4.8,
        ],
        "circle-color": [
          "case",
          ["==", ["get", "survival_status"], "cleared_or_row"],
          "#334155",
          [
            "match",
            ["get", "category"],
            "church",
            "#F59E0B",
            "school",
            "#38BDF8",
            "business",
            "#FB7185",
            "residence",
            "#C084FC",
            "#F59E0B",
          ],
        ],
        "circle-stroke-color": [
          "case",
          ["==", ["get", "survival_status"], "surviving_era_structure"],
          "#FEF3C7",
          ["==", ["get", "survival_status"], "cleared_or_row"],
          [
            "match",
            ["get", "category"],
            "church",
            "#F59E0B",
            "school",
            "#38BDF8",
            "business",
            "#FB7185",
            "residence",
            "#C084FC",
            "#94A3B8",
          ],
          "#0F172A",
        ],
        "circle-stroke-width": [
          "match",
          ["get", "survival_status"],
          "surviving_era_structure",
          2.2,
          "cleared_or_row",
          1.8,
          1.6,
        ],
      },
    });
    this.map.addLayer({
      id: "relocated-structures-glow",
      type: "circle",
      source: "relocated-structures-src",
      paint: {
        "circle-radius": 9.5,
        "circle-color": [
          "case",
          ["boolean", ["get", "was_relocated"], false],
          "#38BDF8",
          "#F59E0B",
        ],
        "circle-opacity": 0.34,
        "circle-blur": 0.45,
      },
    });
    this.map.addLayer({
      id: "relocated-structures-circle",
      type: "circle",
      source: "relocated-structures-src",
      paint: {
        "circle-radius": 5.8,
        "circle-color": [
          "case",
          ["boolean", ["get", "was_relocated"], false],
          "#38BDF8",
          "#F59E0B",
        ],
        "circle-stroke-color": "#0F172A",
        "circle-stroke-width": 1.8,
      },
    });
    this.map.addLayer({
      id: "permit-watch-outer-glow",
      type: "circle",
      source: "permit-watch-src",
      paint: {
        "circle-radius": [
          "case",
          ["==", ["get", "severity"], "critical"],
          15.5,
          12.0,
        ],
        "circle-color": [
          "case",
          ["==", ["get", "severity"], "critical"],
          "#EF4444",
          "#F59E0B",
        ],
        "circle-opacity": [
          "case",
          ["==", ["get", "severity"], "critical"],
          0.34,
          0.26,
        ],
        "circle-blur": 0.48,
      },
    });
    this.map.addLayer({
      id: "permit-watch-ring",
      type: "circle",
      source: "permit-watch-src",
      paint: {
        "circle-radius": [
          "case",
          ["==", ["get", "severity"], "critical"],
          10.2,
          8.4,
        ],
        "circle-color": "rgba(15, 23, 42, 0.0)",
        "circle-stroke-color": [
          "case",
          ["==", ["get", "severity"], "critical"],
          "#FCA5A5",
          "#FDE68A",
        ],
        "circle-stroke-width": 1.8,
        "circle-stroke-opacity": 0.92,
      },
    });
    this.map.addLayer({
      id: "permit-watch-circle",
      type: "circle",
      source: "permit-watch-src",
      paint: {
        "circle-radius": [
          "case",
          ["==", ["get", "severity"], "critical"],
          6.8,
          5.8,
        ],
        "circle-color": [
          "case",
          ["==", ["get", "severity"], "critical"],
          "#EF4444",
          "#F59E0B",
        ],
        "circle-stroke-color": "#FEF2F2",
        "circle-stroke-width": 2.0,
      },
    });
    this.map.addLayer({
      id: "plat-watch-outer-glow",
      type: "circle",
      source: "plat-watch-src",
      layout: { visibility: "none" },
      paint: {
        "circle-radius": [
          "case",
          ["==", ["get", "severity"], "critical"],
          13.0,
          10.2,
        ],
        "circle-color": [
          "case",
          ["==", ["get", "severity"], "critical"],
          "#F43F5E",
          "#8B5CF6",
        ],
        "circle-opacity": 0.28,
        "circle-blur": 0.45,
      },
    });
    this.map.addLayer({
      id: "plat-watch-ring",
      type: "circle",
      source: "plat-watch-src",
      layout: { visibility: "none" },
      paint: {
        "circle-radius": [
          "case",
          ["==", ["get", "severity"], "critical"],
          9.4,
          7.6,
        ],
        "circle-color": "rgba(15, 23, 42, 0.0)",
        "circle-stroke-color": [
          "case",
          ["==", ["get", "severity"], "critical"],
          "#FDA4AF",
          "#C4B5FD",
        ],
        "circle-stroke-width": 1.6,
        "circle-stroke-opacity": 0.9,
      },
    });
    this.map.addLayer({
      id: "plat-watch-circle",
      type: "circle",
      source: "plat-watch-src",
      layout: { visibility: "none" },
      paint: {
        "circle-radius": [
          "case",
          ["==", ["get", "severity"], "critical"],
          6.2,
          5.2,
        ],
        "circle-color": [
          "case",
          ["==", ["get", "severity"], "critical"],
          "#F43F5E",
          "#8B5CF6",
        ],
        "circle-stroke-color": "#FFF1F2",
        "circle-stroke-width": 1.8,
      },
    });
    this.map.addLayer({
      id: "hahc-coa-glow",
      type: "circle",
      source: "hahc-coa-src",
      ...(hahcFilterExpr ? { filter: hahcFilterExpr } : {}),
      layout: { visibility: state.layers?.hahcTracker ? "visible" : "none" },
      paint: {
        "circle-radius": [
          "case",
          ["boolean", ["get", "has_active_agenda"], false],
          13.5,
          ["boolean", ["get", "has_demolition_relocation"], false],
          12.5,
          10.2,
        ],
        "circle-color": [
          "case",
          ["boolean", ["get", "has_active_agenda"], false],
          "#F43F5E",
          ["boolean", ["get", "has_demolition_relocation"], false],
          "#EF4444",
          ["boolean", ["get", "has_designation"], false],
          "#A855F7",
          ["boolean", ["get", "has_denied_or_cor"], false],
          "#F59E0B",
          "#14B8A6",
        ],
        "circle-opacity": 0.32,
        "circle-blur": 0.45,
      },
    });
    this.map.addLayer({
      id: "hahc-coa-circle",
      type: "circle",
      source: "hahc-coa-src",
      ...(hahcFilterExpr ? { filter: hahcFilterExpr } : {}),
      layout: { visibility: state.layers?.hahcTracker ? "visible" : "none" },
      paint: {
        "circle-radius": [
          "case",
          ["boolean", ["get", "has_active_agenda"], false],
          6.8,
          ["boolean", ["get", "has_demolition_relocation"], false],
          6.4,
          5.5,
        ],
        "circle-color": [
          "case",
          ["boolean", ["get", "has_active_agenda"], false],
          "#F43F5E",
          ["boolean", ["get", "has_demolition_relocation"], false],
          "#EF4444",
          ["boolean", ["get", "has_designation"], false],
          "#A855F7",
          ["boolean", ["get", "has_denied_or_cor"], false],
          "#F59E0B",
          "#14B8A6",
        ],
        "circle-stroke-color": [
          "case",
          ["boolean", ["get", "has_active_agenda"], false],
          "#FFE4E6",
          ["boolean", ["get", "has_designation"], false],
          "#F3E8FF",
          "#0F172A",
        ],
        "circle-stroke-width": [
          "case",
          ["boolean", ["get", "has_active_agenda"], false],
          2.2,
          1.8,
        ],
      },
    });
    this.map.addLayer({
      id: "portfolio-works-glow",
      type: "circle",
      source: "portfolio-works-src",
      paint: {
        "circle-radius": [
          "case",
          ["boolean", ["get", "is_active"], false],
          16.5,
          12.0,
        ],
        "circle-color": [
          "case",
          ["boolean", ["get", "is_lost"], false],
          "#F472B6",
          "#FBBF24",
        ],
        "circle-opacity": [
          "case",
          ["boolean", ["get", "is_active"], false],
          0.52,
          0.34,
        ],
        "circle-blur": 0.45,
      },
    });
    this.map.addLayer({
      id: "portfolio-works-circle",
      type: "circle",
      source: "portfolio-works-src",
      paint: {
        "circle-radius": [
          "case",
          ["boolean", ["get", "is_active"], false],
          8.8,
          6.8,
        ],
        "circle-color": [
          "case",
          ["boolean", ["get", "is_lost"], false],
          "#EC4899",
          "#F59E0B",
        ],
        "circle-stroke-color": [
          "case",
          ["boolean", ["get", "is_active"], false],
          "#FFFFFF",
          ["boolean", ["get", "is_lost"], false],
          "#FDE047",
          "#FEF3C7",
        ],
        "circle-stroke-width": [
          "case",
          ["boolean", ["get", "is_active"], false],
          2.6,
          2.0,
        ],
      },
    });
    this.map.addLayer({
      id: "historical-railroads-depots",
      type: "circle",
      source: "historical-railroads-src",
      filter: ["==", ["get", "rail_type"], "depot"],
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 4.8, 15, 7.6],
        "circle-color": "#FDE047",
        "circle-stroke-color": "#0F172A",
        "circle-stroke-width": 2.2,
      },
    });
    this.map.addLayer({
      id: "super-neighborhoods-label",
      type: "symbol",
      source: "super-neighborhoods-labels-src",
      minzoom: 10.5,
      layout: {
        "text-field": ["get", "name"],
        "text-font": ["Noto Sans Bold"],
        "text-size": ["interpolate", ["linear"], ["zoom"], 10.5, 10.5, 14, 13.5],
        "text-transform": "uppercase",
        "text-letter-spacing": 0.08,
        "text-max-width": 9,
        "text-padding": 6,
      },
      paint: {
        "text-color": "#93C5FD",
        "text-halo-color": "rgba(11, 15, 23, 0.92)",
        "text-halo-width": 1.8,
      },
    });
    this.map.addLayer({
      id: "neighborhoods-label",
      type: "symbol",
      source: "neighborhoods-labels-src",
      minzoom: 12.2,
      layout: {
        "text-field": ["get", "name"],
        "text-font": ["Noto Sans Bold"],
        "text-size": ["interpolate", ["linear"], ["zoom"], 12.2, 10.5, 16, 13.5],
        "text-max-width": 8,
        "text-padding": 4,
      },
      paint: {
        "text-color": "#D9F99D",
        "text-halo-color": "rgba(11, 15, 23, 0.92)",
        "text-halo-width": 1.8,
      },
    });
    this.map.addLayer({
      id: "platted-subdivisions-label",
      type: "symbol",
      source: "platted-subdivisions-labels-src",
      minzoom: 13.0,
      layout: {
        "symbol-sort-key": [
          "case",
          ["boolean", ["get", "has_deed_docs"], false],
          1,
          10,
        ],
        "text-field": [
          "case",
          ["boolean", ["get", "has_deed_docs"], false],
          ["concat", "📜 ", ["get", "name"]],
          ["get", "name"],
        ],
        "text-font": ["Noto Sans Bold"],
        "text-size": ["interpolate", ["linear"], ["zoom"], 13.0, 9.8, 16.5, 13.0],
        "text-max-width": 8,
        "text-padding": 5,
      },
      paint: {
        "text-color": [
          "case",
          ["boolean", ["get", "has_deed_docs"], false],
          "#FDE68A",
          "#ECFCCB",
        ],
        "text-halo-color": "rgba(11, 15, 23, 0.94)",
        "text-halo-width": 1.8,
      },
    });
    this.map.addLayer({
      id: "land-use-protections-label",
      type: "symbol",
      source: "land-use-protections-labels-src",
      minzoom: 15.2,
      layout: {
        "text-field": ["get", "name"],
        "text-font": ["Noto Sans Bold"],
        "text-size": ["interpolate", ["linear"], ["zoom"], 15.2, 9.5, 17.0, 12.0],
        "text-max-width": 9,
        "text-padding": 12,
      },
      paint: {
        "text-color": [
          "match",
          ["get", "protection_type"],
          "smbl",
          "#A7F3D0",
          "conservation",
          "#FECDD3",
          "#FDE68A",
        ],
        "text-halo-color": "rgba(11, 15, 23, 0.94)",
        "text-halo-width": 1.8,
      },
    });
    this.map.addLayer({
      id: "historic-wards-label",
      type: "symbol",
      source: "historic-wards-labels-src",
      filter: wardFilterExpr,
      minzoom: 10.5,
      layout: {
        "text-field": [
          "concat",
          ["get", "name"],
          "\n(",
          ["coalesce", ["get", "era_short"], ["to-string", ["get", "era"]]],
          ")",
        ],
        "text-font": ["Noto Sans Bold"],
        "text-size": ["interpolate", ["linear"], ["zoom"], 10.5, 12, 14, 15.5],
        "text-letter-spacing": 0.06,
        "text-max-width": 10,
      },
      paint: {
        "text-color": "#FDE68A",
        "text-halo-color": "rgba(11, 15, 23, 0.94)",
        "text-halo-width": 2.1,
      },
    });
    this.map.addLayer({
      id: "holc-1937-zones-label",
      type: "symbol",
      source: "holc-1937-zones-labels-src",
      ...(holcFilterExpr ? { filter: holcFilterExpr } : {}),
      minzoom: 10.8,
      layout: {
        visibility: state.layers?.holc1937 ? "visible" : "none",
        "text-field": [
          "concat",
          "HOLC ",
          ["coalesce", ["get", "zone_code"], ""],
          " (",
          ["coalesce", ["get", "grade_short"], ["get", "holc_grade"], ""],
          ")\n",
          ["coalesce", ["get", "area_title"], ""],
        ],
        "text-font": ["Noto Sans Bold"],
        "text-size": ["interpolate", ["linear"], ["zoom"], 10.8, 10.2, 14.5, 13.5],
        "text-max-width": 10,
        "text-padding": 5,
      },
      paint: {
        "text-color": [
          "match",
          ["get", "holc_grade"],
          "A",
          "#86EFAC",
          "B",
          "#BAE6FD",
          "C",
          "#FEF08A",
          "D",
          "#FECACA",
          "#E2E8F0",
        ],
        "text-halo-color": "rgba(11, 15, 23, 0.94)",
        "text-halo-width": 2.0,
      },
    });
    this.map.addLayer({
      id: "historical-waterways-label",
      type: "symbol",
      source: "historical-waterways-src",
      minzoom: 11.2,
      filter: ["!", ["boolean", ["get", "is_minor_trib"], false]],
      layout: {
        "symbol-placement": "line",
        "text-field": ["get", "name"],
        "text-font": ["Noto Sans Bold"],
        "text-size": ["interpolate", ["linear"], ["zoom"], 11.2, 10.0, 15, 12.5],
        "symbol-spacing": 320,
      },
      paint: {
        "text-color": [
          "match",
          ["get", "waterway_type"],
          "buried_gully",
          "#FDE68A",
          "historic_oxbow",
          "#DDD6FE",
          "#7DD3FC",
        ],
        "text-halo-color": "rgba(11, 15, 23, 0.95)",
        "text-halo-width": 1.9,
      },
    });
    this.map.addLayer({
      id: "historical-railroads-label",
      type: "symbol",
      source: "historical-railroads-src",
      filter: ["!=", ["get", "rail_type"], "depot"],
      minzoom: 11.2,
      layout: {
        "symbol-placement": "line",
        "text-field": ["get", "name"],
        "text-font": ["Noto Sans Bold"],
        "text-size": ["interpolate", ["linear"], ["zoom"], 11.2, 10.0, 15, 12.2],
        "symbol-spacing": 340,
      },
      paint: {
        "text-color": [
          "match",
          ["get", "rail_type"],
          "abandoned_trail",
          "#FDA4AF",
          "streetcar_interurban",
          "#E9D5FF",
          "industrial_spur",
          "#CBD5E1",
          "#FDE68A",
        ],
        "text-halo-color": "rgba(11, 15, 23, 0.95)",
        "text-halo-width": 1.9,
      },
    });
    this.map.addLayer({
      id: "historical-railroads-depots-label",
      type: "symbol",
      source: "historical-railroads-src",
      filter: ["==", ["get", "rail_type"], "depot"],
      minzoom: 11.8,
      layout: {
        "text-field": ["get", "name"],
        "text-font": ["Noto Sans Bold"],
        "text-size": ["interpolate", ["linear"], ["zoom"], 11.8, 10.2, 15.5, 12.5],
        "text-offset": [0, 1.15],
        "text-anchor": "top",
        "text-max-width": 11,
      },
      paint: {
        "text-color": "#FEF08A",
        "text-halo-color": "rgba(11, 15, 23, 0.96)",
        "text-halo-width": 2.0,
      },
    });
    this.map.addLayer({
      id: "lost-houston-label",
      type: "symbol",
      source: "lost-houston-labels-src",
      filter: lostHoustonFilterExpr,
      minzoom: 12.2,
      layout: {
        visibility: showLostHouston ? "visible" : "none",
        "text-field": [
          "concat",
          "† ",
          ["get", "name"],
          "\n(",
          ["to-string", ["get", "year_built"]],
          "-",
          ["to-string", ["get", "year_demolished"]],
          ")",
        ],
        "text-font": ["Noto Sans Bold"],
        "text-size": ["interpolate", ["linear"], ["zoom"], 12.2, 9.8, 16, 12.5],
        "text-max-width": 9,
        "text-padding": 4,
      },
      paint: {
        "text-color": "#FBCFE8",
        "text-halo-color": "rgba(11, 15, 23, 0.95)",
        "text-halo-width": 1.9,
      },
    });
    this.map.addLayer({
      id: "potential-historic-districts-label",
      type: "symbol",
      source: "potential-historic-districts-labels-src",
      minzoom: 12.0,
      layout: {
        visibility: state.layers?.potentialHistoricDistricts ? "visible" : "none",
        "text-field": ["get", "name"],
        "text-font": ["Noto Sans Bold"],
        "text-size": ["interpolate", ["linear"], ["zoom"], 12.0, 9.8, 15.5, 12.6],
        "text-max-width": 9,
        "text-padding": 5,
      },
      paint: {
        "text-color": "#FED7AA",
        "text-halo-color": "rgba(11, 15, 23, 0.94)",
        "text-halo-width": 1.9,
      },
    });
    this.map.addLayer({
      id: "cultural-districts-label",
      type: "symbol",
      source: "cultural-districts-labels-src",
      minzoom: 11.2,
      layout: {
        visibility: state.layers?.culturalDistricts ? "visible" : "none",
        "text-field": ["get", "name"],
        "text-font": ["Noto Sans Bold"],
        "text-size": ["interpolate", ["linear"], ["zoom"], 11.2, 10.2, 15.0, 13.0],
        "text-max-width": 10,
        "text-padding": 6,
      },
      paint: {
        "text-color": "#FBCFE8",
        "text-halo-color": "rgba(11, 15, 23, 0.94)",
        "text-halo-width": 1.9,
      },
    });
  }

  _buildTourFocusFilterExpression(baseFilterExpr) {
    if (!this.activeTour || !this.tourFocusMode || this.tourFocusMode === "all") {
      return baseFilterExpr;
    }
    const stops = Array.isArray(this.activeTour.stops) ? this.activeTour.stops : [];
    const tourHcads = stops
      .map((s) => String(s.hcad_num || "").trim())
      .filter(Boolean);
    const tourBldIds = stops
      .map((s) => String(s.building_id || "").trim())
      .filter(Boolean);

    const stopMatchClauses = [];
    if (tourHcads.length > 0) {
      stopMatchClauses.push(["in", ["get", "hcad_num"], ["literal", tourHcads]]);
    }
    if (tourBldIds.length > 0) {
      stopMatchClauses.push(["in", ["get", "building_id"], ["literal", tourBldIds]]);
      stopMatchClauses.push(["in", ["get", "id"], ["literal", tourBldIds]]);
    }

    if (this.tourFocusMode === "tour_only") {
      if (!stopMatchClauses.length) return baseFilterExpr;
      return ["all", baseFilterExpr, ["any", ...stopMatchClauses]];
    }

    if (this.tourFocusMode === "landmarks") {
      const landmarkClauses = [
        ...stopMatchClauses,
        ["==", ["get", "is_designated_landmark"], true],
        ["in", ["get", "landmark_type"], ["literal", ["Landmark", "Protected Landmark"]]],
        ["!=", ["coalesce", ["get", "good_brick_summary"], ""], ""],
      ];
      return ["all", baseFilterExpr, ["any", ...landmarkClauses]];
    }

    return baseFilterExpr;
  }

  _buildShardLayerFilter(baseFilterExpr) {
    const hcadSet = new Set();
    for (const f of this.overridesFC?.features || []) {
      const p = f.properties || {};
      if (p.keep_shard_footprints) continue;
      if (!p.is_building_override || p.replace_parcel_shards) {
        const primaryHcad = String(p.hcad_num || "").trim();
        if (primaryHcad) hcadSet.add(primaryHcad);
      }
      if (Array.isArray(p.suppress_shard_hcads)) {
        for (const sh of p.suppress_shard_hcads) {
          const cleanSh = String(sh || "").trim();
          if (cleanSh) hcadSet.add(cleanSh);
        }
      }
    }
    for (const [ovKey, ov] of Object.entries(this.curatedOverrides || {})) {
      if (!ov || ov.keep_shard_footprints) continue;
      // Only suppress a shard HCAD before dynamic geometry capture if it is an explicit suppress_only entry
      // or already has baked-in geometry in curatedOverrides.
      if (ov.suppress_only || (ov.geometry && (!ov.is_building_override || ov.replace_parcel_shards))) {
        const primaryHcad = String(ov.hcad_num || ovKey.split("#")[0] || "").trim();
        if (primaryHcad && /^\d+$/.test(primaryHcad)) hcadSet.add(primaryHcad);
      }
      if (Array.isArray(ov.suppress_shard_hcads)) {
        for (const sh of ov.suppress_shard_hcads) {
          const cleanSh = String(sh || "").trim();
          if (cleanSh) hcadSet.add(cleanSh);
        }
      }
    }
    const overriddenHcads = Array.from(hcadSet);
    if (!overriddenHcads.length) return baseFilterExpr;
    return [
      "all",
      baseFilterExpr,
      ["!", ["in", ["get", "hcad_num"], ["literal", overriddenHcads]]],
    ];
  }

  _captureDynamicOverrideGeometries() {
    if (!this.map || !this.shardSourceIds || !this.shardSourceIds.length) return;
    const existingKeys = new Set(
      (this.overridesFC?.features || []).map((f) =>
        String(f.properties?.building_id || f.properties?.id || f.properties?.hcad_num || "").trim()
      )
    );
    const existingHcads = new Set(
      (this.overridesFC?.features || [])
        .filter((f) => !f.properties?.is_building_override)
        .map((f) => String(f.properties?.hcad_num || "").trim())
    );
    const missingHcads = Object.entries(this.curatedOverrides || {})
      .filter(
        ([k, ov]) =>
          k &&
          !ov?.is_building_override &&
          !ov?.keep_shard_footprints &&
          !ov?.suppress_only &&
          !ov?.geometry &&
          !existingKeys.has(k) &&
          !existingHcads.has(k)
      )
      .map(([k]) => k);
    if (!missingHcads.length) return;

    let added = false;
    const seenHitCoords = new Set();
    for (const srcId of this.shardSourceIds) {
      try {
        const hits = this.map.querySourceFeatures(srcId, {
          sourceLayer: "buildings",
          filter: ["in", ["get", "hcad_num"], ["literal", missingHcads]],
        });
        for (const hit of hits) {
          const hcad = String(hit.properties?.hcad_num || "").trim();
          if (hcad && hit.geometry) {
            const firstPt =
              hit.geometry.type === "Polygon"
                ? hit.geometry.coordinates?.[0]?.[0]
                : hit.geometry.coordinates?.[0]?.[0]?.[0];
            const dedupKey = firstPt
              ? `${hcad}:${firstPt[0].toFixed(6)},${firstPt[1].toFixed(6)}`
              : `${hcad}:${this.overridesFC.features.length}`;
            if (seenHitCoords.has(dedupKey)) continue;
            seenHitCoords.add(dedupKey);
            existingHcads.add(hcad);
            if (this.curatedOverrides[hcad] && !this.curatedOverrides[hcad].geometry) {
              this.curatedOverrides[hcad].geometry = hit.geometry;
            }
            this.overridesFC.features.push({
              type: "Feature",
              geometry: hit.geometry,
              properties: applyOverrideToProperties(hit.properties, this.curatedOverrides),
            });
            added = true;
          }
        }
      } catch (_e) {
        // Source tile may not be loaded yet
      }
    }

    if (added && this.map.getSource("curated-overrides-src")) {
      this.map.getSource("curated-overrides-src").setData(this.overridesFC);
      this.syncWithState(this.filterStore.getState());
    }
  }

  _bindMapLibreInteractions() {
    this.map.on("mousemove", (e) => {
      const stack = this._collectOverlappingFeaturesAtPoint(e.point, e.lngLat);
      if (!stack.length) {
        this.map.getCanvas().style.cursor = "";
        this.popup.remove();
        return;
      }
      this.map.getCanvas().style.cursor = "pointer";
      const topItem = stack[0];
      const p = applyOverrideToProperties(topItem.props || {}, this.curatedOverrides);
      this.popup
        .setLngLat(e.lngLat)
        .setHTML(this._buildTooltipHTML(p, stack))
        .addTo(this.map);
    });

    this.map.on("click", (e) => {
      this._handleMapPointClick(e.point, e.lngLat);
    });

    this.map.on("pitch", () => {
      if (this.onPitchChange) {
        this.onPitchChange(Math.round(this.map.getPitch()));
      }
    });

    this.map.on("pitchend", () => {
      const p = Math.round(this.map.getPitch());
      const state = this.filterStore.getState();
      if (p >= 8 && !state.extrude3D) {
        this.filterStore.setState({ extrude3D: true });
      } else if (p < 3 && state.extrude3D) {
        this.filterStore.setState({ extrude3D: false });
      }
      if (this.onPitchChange) {
        this.onPitchChange(p);
      }
    });

    this.map.on("move", () => {
      if (this.spyglassState) {
        this._renderSpyglassOverlay();
      }
    });

    this.map.on("moveend", () => {
      this._captureDynamicOverrideGeometries();
      this._refreshSelectionAfterViewportChange();
      this.computeViewportHistogram();
    });

    this.map.on("idle", () => {
      this._captureDynamicOverrideGeometries();
      this._refreshSelectionAfterViewportChange();
      this.computeViewportHistogram();
    });
  }

  _initSpyglassOverlay() {
    const container = document.getElementById(this.containerId);
    if (!container) return;
    let spCanvas = container.querySelector("#atlas-spyglass-canvas");
    if (!spCanvas) {
      spCanvas = document.createElement("canvas");
      spCanvas.id = "atlas-spyglass-canvas";
      spCanvas.style.position = "absolute";
      spCanvas.style.inset = "0";
      spCanvas.style.width = "100%";
      spCanvas.style.height = "100%";
      spCanvas.style.pointerEvents = "none";
      spCanvas.style.zIndex = "1";
      spCanvas.style.display = "none";
      const ctrlContainer = container.querySelector(".maplibregl-control-container");
      if (ctrlContainer) {
        container.insertBefore(spCanvas, ctrlContainer);
      } else {
        container.appendChild(spCanvas);
      }
    } else {
      spCanvas.style.zIndex = "1";
      const ctrlContainer = container.querySelector(".maplibregl-control-container");
      if (ctrlContainer && spCanvas.nextSibling !== ctrlContainer) {
        container.insertBefore(spCanvas, ctrlContainer);
      }
    }

    const prev = this.spyglassState;
    const initRadius = Number(this.filterStore?.getState?.()?.historicMapSpyglassRadius) || prev?.radius || 152;
    this.spyglassState = {
      canvas: spCanvas,
      ctx: spCanvas.getContext("2d"),
      mouseX: prev ? prev.mouseX : null,
      mouseY: prev ? prev.mouseY : null,
      radius: initRadius,
      tileCache: prev?.tileCache || new Map(),
    };

    const resizeSpyglass = () => {
      if (!this.spyglassState?.canvas) return;
      const dpr = window.devicePixelRatio || 1;
      this.spyglassState.canvas.width = container.clientWidth * dpr;
      this.spyglassState.canvas.height = container.clientHeight * dpr;
      this._renderSpyglassOverlay();
    };
    resizeSpyglass();

    if (!this._spyglassListenersAttached) {
      this._spyglassListenersAttached = true;
      window.addEventListener("resize", resizeSpyglass);

      container.addEventListener("mousemove", (e) => {
        if (!this.spyglassState) return;
        const rect = container.getBoundingClientRect();
        this.spyglassState.mouseX = e.clientX - rect.left;
        this.spyglassState.mouseY = e.clientY - rect.top;
        const st = this.filterStore.getState();
        if (st.layers?.historicMap && st.historicMapSpyglass) {
          this._renderSpyglassOverlay();
        }
      });

      window.addEventListener("keydown", (e) => {
        if (e.metaKey || e.ctrlKey || e.altKey) return;
        const activeEl = document.activeElement;
        if (activeEl) {
          const tag = (activeEl.tagName || "").toUpperCase();
          if (tag === "TEXTAREA" || activeEl.isContentEditable) return;
          if (tag === "INPUT") {
            const inputType = (activeEl.type || "text").toLowerCase();
            if (
              inputType !== "range" &&
              inputType !== "checkbox" &&
              inputType !== "radio" &&
              inputType !== "button"
            ) {
              return;
            }
          }
        }
        const st = this.filterStore?.getState?.();
        if (!st?.layers?.historicMap || !st?.historicMapSpyglass) return;

        let delta = 0;
        if (e.key === "[" || e.key === "{" || e.code === "BracketLeft") {
          delta = -20;
        } else if (e.key === "]" || e.key === "}" || e.code === "BracketRight") {
          delta = 20;
        }
        if (delta !== 0) {
          e.preventDefault();
          const curRadius =
            Number(st.historicMapSpyglassRadius) || this.spyglassState?.radius || 152;
          const nextRadius = Math.max(72, Math.min(360, curRadius + delta));
          if (this.spyglassState) {
            this.spyglassState.radius = nextRadius;
          }
          this.filterStore.setState({ historicMapSpyglassRadius: nextRadius });
          this._renderSpyglassOverlay();
        }
      });
    }
  }

  _renderSpyglassOverlay() {
    const sp = this.spyglassState;
    if (!sp || !sp.canvas) return;
    const state = this.filterStore.getState();
    if (!state.layers?.historicMap || !state.historicMapSpyglass) {
      sp.canvas.style.display = "none";
      return;
    }
    sp.canvas.style.display = "block";
    const dpr = window.devicePixelRatio || 1;
    const width = sp.canvas.width / dpr;
    const height = sp.canvas.height / dpr;
    if (width <= 0 || height <= 0) return;

    const ctx = sp.ctx;
    ctx.save();
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, width, height);

    const cx = Number.isFinite(sp.mouseX) ? sp.mouseX : width * 0.52;
    const cy = Number.isFinite(sp.mouseY) ? sp.mouseY : height * 0.48;
    const r = Math.max(72, Math.min(360, Number(state.historicMapSpyglassRadius) || sp.radius || 152));
    sp.radius = r;

    let vpLng = -95.3635;
    let vpLat = 29.7595;
    let vpZoom = 15.2;
    const isWebGLMap = !this.useCanvasFallback && Boolean(this.map);
    if (this.useCanvasFallback && this.canvasState) {
      vpLng = this.canvasState.lng;
      vpLat = this.canvasState.lat;
      vpZoom = this.canvasState.zoom;
    } else if (this.map) {
      const c = this.map.getCenter();
      vpLng = c.lng;
      vpLat = c.lat;
      vpZoom = this.map.getZoom();
    }

    // MapLibre GL JS uses a 512px world size at zoom 0 (worldSize = 512 * 2^zoom = 256 * 2^(zoom + 1)),
    // whereas the 2D Canvas fallback uses 256 * 2^zoom.
    const effectiveTileZoom = isWebGLMap ? vpZoom + 1 : vpZoom;
    const histEntry = getHistoricalMapEntry(state.historicMapId);
    const minTileZ = Number(histEntry.minZoom || histEntry.minzoom) || 10;
    const maxTileZ = Number(histEntry.maxZoom || histEntry.maxzoom) || 19;
    const histZ = Math.max(minTileZ, Math.min(maxTileZ, Math.floor(effectiveTileZoom)));
    const nTiles = Math.pow(2, histZ);

    // Find exact geographic coordinate directly beneath the center of the spyglass lens (cx, cy)
    let lensLng = vpLng;
    let lensLat = vpLat;
    if (isWebGLMap) {
      try {
        const unproj = this.map.unproject([cx, cy]);
        if (unproj && Number.isFinite(unproj.lng) && Number.isFinite(unproj.lat)) {
          lensLng = unproj.lng;
          lensLat = unproj.lat;
        }
      } catch (_e) {}
    } else if (this.useCanvasFallback) {
      const unproj = this._screenToLngLat(cx, cy, width, height);
      if (unproj && Number.isFinite(unproj.lng) && Number.isFinite(unproj.lat)) {
        lensLng = unproj.lng;
        lensLat = unproj.lat;
      }
    }

    const sinLensLat = Math.sin((Math.max(-85.0511, Math.min(85.0511, lensLat)) * Math.PI) / 180);
    const lensTileX = ((lensLng + 180) / 360) * nTiles;
    const lensTileY =
      (0.5 - Math.log((1 + sinLensLat) / (1 - sinLensLat)) / (4 * Math.PI)) * nTiles;
    const baseTx = Math.floor(lensTileX);
    const baseTy = Math.floor(lensTileY);

    const tileXToLng = (x) => (x / nTiles) * 360 - 180;
    const tileYToLat = (y) => {
      const n = Math.PI - (2 * Math.PI * y) / nTiles;
      return (180 / Math.PI) * Math.atan(0.5 * (Math.exp(n) - Math.exp(-n)));
    };
    const projectLngLat = (lng, lat) => {
      if (isWebGLMap) {
        const pt = this.map.project([lng, lat]);
        return [pt.x, pt.y];
      }
      return this._lngLatToScreen(lng, lat, width, height);
    };

    const approxTileScreenPx = Math.max(48, 256 * Math.pow(2, effectiveTileZoom - histZ));
    const hCols = Math.min(5, Math.ceil(r / approxTileScreenPx) + 1);
    const hRows = Math.min(5, Math.ceil(r / approxTileScreenPx) + 1);

    // Clip to spyglass circle
    ctx.save();
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.clip();

    ctx.fillStyle = "#181512";
    ctx.fillRect(cx - r, cy - r, r * 2, r * 2);

    ctx.globalAlpha = Math.max(
      0.15,
      Math.min(1, (Number(state.historicMapOpacity) || 82) / 100)
    );

    const maxT = 1 << histZ;
    for (let dx = -hCols; dx <= hCols; dx++) {
      for (let dy = -hRows; dy <= hRows; dy++) {
        const tx = baseTx + dx;
        const ty = baseTy + dy;
        if (tx < 0 || ty < 0 || tx >= maxT || ty >= maxT) continue;

        const lngWest = tileXToLng(tx);
        const lngEast = tileXToLng(tx + 1);
        const latNorth = tileYToLat(ty);
        const latSouth = tileYToLat(ty + 1);

        const [nwX, nwY] = projectLngLat(lngWest, latNorth);
        const [neX, neY] = projectLngLat(lngEast, latNorth);
        const [swX, swY] = projectLngLat(lngWest, latSouth);
        const [seX, seY] = projectLngLat(lngEast, latSouth);

        const minX = Math.min(nwX, neX, swX, seX);
        const maxX = Math.max(nwX, neX, swX, seX);
        const minY = Math.min(nwY, neY, swY, seY);
        const maxY = Math.max(nwY, neY, swY, seY);
        if (maxX < cx - r || minX > cx + r || maxY < cy - r || minY > cy + r) {
          continue;
        }

        const hCacheId = `spy:${histEntry.id}:${histZ}/${tx}/${ty}`;
        let hImg = sp.tileCache.get(hCacheId);
        if (!hImg) {
          hImg = new Image();
          hImg.crossOrigin = "anonymous";
          hImg.src = formatHistoricalTileUrl(histEntry.tileUrl, histZ, tx, ty);
          hImg.onload = () => this._renderSpyglassOverlay();
          sp.tileCache.set(hCacheId, hImg);
        }
        if (hImg.complete && hImg.naturalWidth > 0) {
          const imgW = hImg.naturalWidth;
          const imgH = hImg.naturalHeight;
          ctx.save();
          ctx.transform(
            (neX - nwX) / imgW,
            (neY - nwY) / imgW,
            (swX - nwX) / imgH,
            (swY - nwY) / imgH,
            nwX,
            nwY
          );
          ctx.drawImage(hImg, 0, 0, imgW + 0.4, imgH + 0.4);
          ctx.restore();
        }
      }
    }
    ctx.restore();

    // Draw archival brass/gold spyglass bezel & reticle ticks
    ctx.beginPath();
    ctx.arc(cx, cy, r + 2, 0, Math.PI * 2);
    ctx.strokeStyle = "rgba(11, 15, 23, 0.88)";
    ctx.lineWidth = 6;
    ctx.stroke();

    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.strokeStyle = "#FDE047";
    ctx.lineWidth = 2.6;
    ctx.stroke();

    // Subtle compass ticks on the spyglass ring
    const tickAngles = [0, Math.PI / 2, Math.PI, (Math.PI * 3) / 2];
    ctx.strokeStyle = "rgba(253, 224, 71, 0.85)";
    ctx.lineWidth = 1.6;
    for (const ang of tickAngles) {
      ctx.beginPath();
      ctx.moveTo(cx + Math.cos(ang) * (r - 8), cy + Math.sin(ang) * (r - 8));
      ctx.lineTo(cx + Math.cos(ang) * (r + 3), cy + Math.sin(ang) * (r + 3));
      ctx.stroke();
    }

    // Bottom pill label showing active historical map year & title
    const pillText = `🔍 ${histEntry.year} · ${histEntry.shortLabel}`;
    ctx.font = "700 11px Inter, system-ui, sans-serif";
    const textW = ctx.measureText(pillText).width;
    const pillW = textW + 18;
    const pillH = 22;
    const pillX = cx - pillW / 2;
    const pillY = cy + r - 14;

    ctx.fillStyle = "rgba(11, 15, 23, 0.92)";
    ctx.strokeStyle = "rgba(253, 224, 71, 0.78)";
    ctx.lineWidth = 1.3;
    ctx.beginPath();
    if (typeof ctx.roundRect === "function") {
      ctx.roundRect(pillX, pillY, pillW, pillH, 11);
    } else {
      ctx.rect(pillX, pillY, pillW, pillH);
    }
    ctx.fill();
    ctx.stroke();

    ctx.fillStyle = "#FEF08A";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(pillText, cx, pillY + pillH / 2 + 0.5);

    ctx.restore();
  }

  _syncWebGLHistoricMapSource(state) {
    if (!this.map) return;
    const entry = getHistoricalMapEntry(state.historicMapId);
    if (this._activeWebGLHistoricMapId === entry.id) return;
    this._activeWebGLHistoricMapId = entry.id;
    const src = this.map.getSource("basemap-historic-topo");
    if (src && typeof src.setTiles === "function") {
      try {
        src.setTiles([entry.tileUrl]);
        return;
      } catch (_e) {}
    }
    try {
      if (this.map.getLayer("basemap-historic-topo-layer")) {
        this.map.removeLayer("basemap-historic-topo-layer");
      }
      if (this.map.getSource("basemap-historic-topo")) {
        this.map.removeSource("basemap-historic-topo");
      }
      this.map.addSource("basemap-historic-topo", {
        type: "raster",
        tiles: [entry.tileUrl],
        tileSize: 256,
        maxzoom: entry.maxZoom || 19,
        attribution: entry.attribution || "",
      });
      const beforeId = this.map.getLayer("vector-roads-highzoom")
        ? "vector-roads-highzoom"
        : undefined;
      this.map.addLayer(
        {
          id: "basemap-historic-topo-layer",
          type: "raster",
          source: "basemap-historic-topo",
          layout: {
            visibility:
              state.layers?.historicMap && !state.historicMapSpyglass
                ? "visible"
                : "none",
          },
          paint: {
            "raster-opacity": Math.max(
              0.1,
              Math.min(1, (Number(state.historicMapOpacity) || 82) / 100)
            ),
            "raster-contrast": 0.08,
          },
        },
        beforeId
      );
    } catch (err) {
      console.warn("Could not switch WebGL historic map source:", err);
    }
  }

  /* ========================================================================
     HTML5 2D Canvas Cartographic Vector & Tile Engine (Fallback Mode)
     ======================================================================== */
  _initCanvas2DEngine(center, zoom, pitch) {
    this.useCanvasFallback = true;
    const container = document.getElementById(this.containerId);
    container.innerHTML = "";

    const canvas = document.createElement("canvas");
    canvas.id = "atlas-2d-canvas";
    canvas.style.width = "100%";
    canvas.style.height = "100%";
    canvas.style.display = "block";
    canvas.style.cursor = "grab";
    container.appendChild(canvas);

    const tooltipEl = document.createElement("div");
    tooltipEl.id = "canvas-hover-tooltip";
    tooltipEl.className = "atlas-hover-popup hidden";
    tooltipEl.style.position = "absolute";
    tooltipEl.style.pointerEvents = "none";
    tooltipEl.style.zIndex = "30";
    container.appendChild(tooltipEl);

    this.canvasState = {
      canvas,
      ctx: canvas.getContext("2d"),
      tooltipEl,
      lng: center[0],
      lat: center[1],
      zoom: zoom,
      pitch: pitch || 0,
      tileCache: new Map(),
      renderedBBoxes: [],
      isDragging: false,
      dragStartX: 0,
      dragStartY: 0,
    };

    const resize = () => {
      const dpr = window.devicePixelRatio || 1;
      canvas.width = container.clientWidth * dpr;
      canvas.height = container.clientHeight * dpr;
      this._renderCanvas2D();
    };
    window.addEventListener("resize", resize);
    resize();
    this._initSpyglassOverlay();

    // Pan & Zoom interactions
    canvas.addEventListener("mousedown", (e) => {
      this.canvasState.isDragging = true;
      this.canvasState.dragStartX = e.clientX;
      this.canvasState.dragStartY = e.clientY;
      this.canvasState.moved = false;
      canvas.style.cursor = "grabbing";
    });

    window.addEventListener("mousemove", (e) => {
      if (!this.canvasState) return;
      if (this.canvasState.isDragging) {
        const dx = e.clientX - this.canvasState.dragStartX;
        const dy = e.clientY - this.canvasState.dragStartY;
        if (Math.abs(dx) > 2 || Math.abs(dy) > 2) this.canvasState.moved = true;
        this.canvasState.dragStartX = e.clientX;
        this.canvasState.dragStartY = e.clientY;

        const scale = 256 * Math.pow(2, this.canvasState.zoom);
        const dLng = (-dx / scale) * 360;
        const cosLat = Math.cos((this.canvasState.lat * Math.PI) / 180);
        const dLat = (dy / scale) * 360 * cosLat;
        this.canvasState.lng += dLng;
        this.canvasState.lat = Math.max(-85, Math.min(85, this.canvasState.lat + dLat));
        this._renderCanvas2D();
        return;
      }

      const rect = canvas.getBoundingClientRect();
      const px = e.clientX - rect.left;
      const py = e.clientY - rect.top;
      if (px < 0 || py < 0 || px > rect.width || py > rect.height) {
        tooltipEl.classList.add("hidden");
        return;
      }
      const hit = this._hitTestCanvas2D(px, py);
      if (hit && hit.isTourStop) {
        canvas.style.cursor = "pointer";
        tooltipEl.innerHTML = `<div class="maplibregl-popup-content">${this._buildTooltipHTML(hit)}</div>`;
        tooltipEl.style.left = `${Math.min(rect.width - 240, px + 14)}px`;
        tooltipEl.style.top = `${Math.max(12, py - 68)}px`;
        tooltipEl.classList.remove("hidden");
        return;
      }
      const lngLat = this._screenToLngLat(px, py, rect.width, rect.height);
      const stack = this._collectOverlappingFeaturesAtPoint({ x: px, y: py }, lngLat);
      if (stack.length > 0) {
        canvas.style.cursor = "pointer";
        const topProps = applyOverrideToProperties(stack[0].props || {}, this.curatedOverrides);
        tooltipEl.innerHTML = `<div class="maplibregl-popup-content">${this._buildTooltipHTML(
          topProps,
          stack
        )}</div>`;
        tooltipEl.style.left = `${Math.min(rect.width - 240, px + 14)}px`;
        tooltipEl.style.top = `${Math.max(12, py - 68)}px`;
        tooltipEl.classList.remove("hidden");
      } else {
        canvas.style.cursor = "grab";
        tooltipEl.classList.add("hidden");
      }
    });

    window.addEventListener("mouseup", (e) => {
      if (!this.canvasState || !this.canvasState.isDragging) return;
      this.canvasState.isDragging = false;
      canvas.style.cursor = "grab";
      if (!this.canvasState.moved) {
        const rect = canvas.getBoundingClientRect();
        const px = e.clientX - rect.left;
        const py = e.clientY - rect.top;
        const hit = this._hitTestCanvas2D(px, py);
        if (hit && hit.isTourStop && typeof this.onSelectTourStop === "function") {
          this.onSelectTourStop(hit.stopIndex);
          return;
        }
        const lngLat = this._screenToLngLat(px, py, rect.width, rect.height);
        this._handleMapPointClick({ x: px, y: py }, lngLat);
      } else {
        this.computeViewportHistogram();
      }
    });

    canvas.addEventListener(
      "wheel",
      (e) => {
        e.preventDefault();
        const delta = e.deltaY < 0 ? 0.35 : -0.35;
        this.canvasState.zoom = Math.max(10.5, Math.min(18.5, this.canvasState.zoom + delta));
        this._renderCanvas2D();
        this.computeViewportHistogram();
      },
      { passive: false }
    );
  }

  _lngLatToScreen(lng, lat, width, height) {
    const cs = this.canvasState;
    const scale = 256 * Math.pow(2, cs.zoom);
    const worldX = ((lng + 180) / 360) * scale;
    const sinLat = Math.sin((lat * Math.PI) / 180);
    const worldY = (0.5 - Math.log((1 + sinLat) / (1 - sinLat)) / (4 * Math.PI)) * scale;

    const centerX = ((cs.lng + 180) / 360) * scale;
    const sinCenter = Math.sin((cs.lat * Math.PI) / 180);
    const centerY =
      (0.5 - Math.log((1 + sinCenter) / (1 - sinCenter)) / (4 * Math.PI)) * scale;

    return [width / 2 + (worldX - centerX), height / 2 + (worldY - centerY)];
  }

  _screenToLngLat(px, py, width, height) {
    const cs = this.canvasState;
    if (!cs) return null;
    const scale = 256 * Math.pow(2, cs.zoom);
    const centerX = ((cs.lng + 180) / 360) * scale;
    const sinCenter = Math.sin((cs.lat * Math.PI) / 180);
    const centerY =
      (0.5 - Math.log((1 + sinCenter) / (1 - sinCenter)) / (4 * Math.PI)) * scale;
    const worldX = centerX + (px - width / 2);
    const worldY = centerY + (py - height / 2);
    const lng = (worldX / scale) * 360 - 180;
    const n = Math.PI - (2 * Math.PI * worldY) / scale;
    const lat = (180 / Math.PI) * Math.atan(0.5 * (Math.exp(n) - Math.exp(-n)));
    return { lng, lat };
  }

  _getCanvasBounds() {
    const cs = this.canvasState;
    const dpr = window.devicePixelRatio || 1;
    const w = cs.canvas.width / dpr;
    const h = cs.canvas.height / dpr;
    const scale = 256 * Math.pow(2, cs.zoom);
    const halfLng = ((w / 2) / scale) * 360;
    const cosLat = Math.cos((cs.lat * Math.PI) / 180);
    const halfLat = ((h / 2) / scale) * 360 * cosLat;
    return {
      getWest: () => cs.lng - halfLng,
      getEast: () => cs.lng + halfLng,
      getSouth: () => cs.lat - halfLat,
      getNorth: () => cs.lat + halfLat,
    };
  }

  _hitTestCanvas2D(px, py) {
    if (!this.canvasState) return null;
    const boxes = this.canvasState.renderedBBoxes;
    for (let i = boxes.length - 1; i >= 0; i--) {
      const b = boxes[i];
      if (px >= b.minX && px <= b.maxX && py >= b.minY && py <= b.maxY) {
        return b.props;
      }
    }
    return null;
  }

  _renderCanvas2D() {
    if (!this.canvasState) return;
    const cs = this.canvasState;
    const state = this.filterStore.getState();
    const dpr = window.devicePixelRatio || 1;
    const width = cs.canvas.width / dpr;
    const height = cs.canvas.height / dpr;
    const ctx = cs.ctx;
    const sinLat = Math.sin((cs.lat * Math.PI) / 180);

    ctx.save();
    ctx.scale(dpr, dpr);

    // 1. Background & Basemap Slippy Tiles
    const isNoMap = state.basemap === "solid_dark" || state.basemap === "solid_light";
    ctx.fillStyle =
      state.basemap === "solid_light"
        ? "#F4F1EA"
        : state.basemap === "warm_parchment"
        ? "#EBE6DC"
        : "#0D1117";
    ctx.fillRect(0, 0, width, height);

    if (!isNoMap) {
      const baseKey = state.basemap || "dark_archival";
      const baseConfig = BASEMAP_TILES[baseKey] || BASEMAP_TILES.dark_archival;
      const maxTileZ = baseConfig.maxZoom || 16;
      const tileZ = Math.max(10, Math.min(maxTileZ, Math.floor(cs.zoom)));
      const zoomFactor = Math.pow(2, cs.zoom - tileZ);
      const tileSizeScreen = 256 * zoomFactor;

      const centerTileX = ((cs.lng + 180) / 360) * Math.pow(2, tileZ);
      const centerTileY =
        (0.5 - Math.log((1 + sinLat) / (1 - sinLat)) / (4 * Math.PI)) * Math.pow(2, tileZ);

      const colsHalf = Math.ceil(width / tileSizeScreen / 2) + 1;
      const rowsHalf = Math.ceil(height / tileSizeScreen / 2) + 1;
      const tileTemplates = baseConfig.tiles;
      const labelTemplates = baseConfig.labelTiles || [];

      for (let dx = -colsHalf; dx <= colsHalf; dx++) {
        for (let dy = -rowsHalf; dy <= rowsHalf; dy++) {
          const tx = Math.floor(centerTileX) + dx;
          const ty = Math.floor(centerTileY) + dy;
          const maxT = 1 << tileZ;
          if (tx < 0 || ty < 0 || tx >= maxT || ty >= maxT) continue;

          const screenX = width / 2 + (tx - centerTileX) * tileSizeScreen;
          const screenY = height / 2 + (ty - centerTileY) * tileSizeScreen;
          const cacheId = `${baseKey}:${tileZ}/${tx}/${ty}`;

          let img = cs.tileCache.get(cacheId);
          if (!img) {
            img = new Image();
            img.crossOrigin = "anonymous";
            const tpl = tileTemplates[Math.abs(tx + ty) % tileTemplates.length];
            img.src = tpl.replace("{z}", tileZ).replace("{x}", tx).replace("{y}", ty);
            img.onload = () => this._renderCanvas2D();
            cs.tileCache.set(cacheId, img);
          }
          if (img.complete && img.naturalWidth > 0) {
            ctx.drawImage(img, screenX, screenY, tileSizeScreen + 0.5, tileSizeScreen + 0.5);
          }

          if (labelTemplates.length > 0) {
            const lblCacheId = `${baseKey}_lbl:${tileZ}/${tx}/${ty}`;
            let lblImg = cs.tileCache.get(lblCacheId);
            if (!lblImg) {
              lblImg = new Image();
              lblImg.crossOrigin = "anonymous";
              const ltpl = labelTemplates[Math.abs(tx + ty) % labelTemplates.length];
              lblImg.src = ltpl.replace("{z}", tileZ).replace("{x}", tx).replace("{y}", ty);
              lblImg.onload = () => this._renderCanvas2D();
              cs.tileCache.set(lblCacheId, lblImg);
            }
            if (lblImg.complete && lblImg.naturalWidth > 0) {
              ctx.drawImage(lblImg, screenX, screenY, tileSizeScreen + 0.5, tileSizeScreen + 0.5);
            }
          }
        }
      }
    }

    if (state.layers?.historicMap && !state.historicMapSpyglass) {
      const histEntry = getHistoricalMapEntry(state.historicMapId);
      const histZ = Math.max(10, Math.min(histEntry.maxZoom || 19, Math.floor(cs.zoom)));
      const histFactor = Math.pow(2, cs.zoom - histZ);
      const histSizeScreen = 256 * histFactor;
      const histCenterX = ((cs.lng + 180) / 360) * Math.pow(2, histZ);
      const histCenterY =
        (0.5 - Math.log((1 + sinLat) / (1 - sinLat)) / (4 * Math.PI)) * Math.pow(2, histZ);
      const hCols = Math.ceil(width / histSizeScreen / 2) + 1;
      const hRows = Math.ceil(height / histSizeScreen / 2) + 1;
      ctx.save();
      ctx.globalAlpha = Math.max(0.1, Math.min(1, (Number(state.historicMapOpacity) || 82) / 100));
      for (let dx = -hCols; dx <= hCols; dx++) {
        for (let dy = -hRows; dy <= hRows; dy++) {
          const tx = Math.floor(histCenterX) + dx;
          const ty = Math.floor(histCenterY) + dy;
          const maxT = 1 << histZ;
          if (tx < 0 || ty < 0 || tx >= maxT || ty >= maxT) continue;
          const sx = width / 2 + (tx - histCenterX) * histSizeScreen;
          const sy = height / 2 + (ty - histCenterY) * histSizeScreen;
          const hCacheId = `hist_${histEntry.id}:${histZ}/${tx}/${ty}`;
          let hImg = cs.tileCache.get(hCacheId);
          if (!hImg) {
            hImg = new Image();
            hImg.crossOrigin = "anonymous";
            hImg.src = formatHistoricalTileUrl(histEntry.tileUrl, histZ, tx, ty);
            hImg.onload = () => this._renderCanvas2D();
            cs.tileCache.set(hCacheId, hImg);
          }
          if (hImg.complete && hImg.naturalWidth > 0) {
            ctx.drawImage(hImg, sx, sy, histSizeScreen + 0.5, histSizeScreen + 0.5);
          }
        }
      }
      ctx.restore();
    }

    const bounds = this._getCanvasBounds();
    const west = bounds.getWest();
    const east = bounds.getEast();
    const south = bounds.getSouth();
    const north = bounds.getNorth();

    const drawPolygonFeature = (geom, fillStyle, strokeStyle, lineWidth = 1, dash = null, extrudeM = 0) => {
      if (!geom || !geom.coordinates) return null;
      const polys = geom.type === "Polygon" ? [geom.coordinates] : geom.coordinates;
      let minX = Infinity,
        minY = Infinity,
        maxX = -Infinity,
        maxY = -Infinity;

      for (const poly of polys) {
        const ring = poly[0];
        if (!ring || ring.length < 3) continue;
        const [fLon, fLat] = ring[0];
        if (fLon < west - 0.02 || fLon > east + 0.02 || fLat < south - 0.02 || fLat > north + 0.02) {
          continue;
        }

        const pts = ring.map(([lon, lat]) => {
          const [sx, sy] = this._lngLatToScreen(lon, lat, width, height);
          if (sx < minX) minX = sx;
          if (sx > maxX) maxX = sx;
          if (sy < minY) minY = sy;
          if (sy > maxY) maxY = sy;
          return [sx, sy];
        });

        if (extrudeM > 0) {
          const roofOffset = Math.min(38, Math.max(4, extrudeM * 0.9));
          // Draw side walls
          ctx.beginPath();
          for (let i = 0; i < pts.length; i++) {
            const [x, y] = pts[i];
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
          }
          ctx.closePath();
          ctx.fillStyle = "rgba(8, 10, 14, 0.55)";
          ctx.fill();

          // Draw elevated roof polygon
          ctx.beginPath();
          for (let i = 0; i < pts.length; i++) {
            const [x, y] = pts[i];
            const rx = x + roofOffset * 0.35;
            const ry = y - roofOffset;
            if (i === 0) ctx.moveTo(rx, ry);
            else ctx.lineTo(rx, ry);
          }
          ctx.closePath();
          if (fillStyle) {
            ctx.fillStyle = fillStyle;
            ctx.fill();
          }
          if (strokeStyle) {
            ctx.strokeStyle = strokeStyle;
            ctx.lineWidth = lineWidth;
            ctx.stroke();
          }
          minY -= roofOffset;
          maxX += roofOffset * 0.35;
        } else {
          ctx.beginPath();
          for (let rIdx = 0; rIdx < poly.length; rIdx++) {
            const subRing = poly[rIdx];
            if (!subRing || subRing.length < 3) continue;
            const ringPts =
              rIdx === 0
                ? pts
                : subRing.map(([lon, lat]) => this._lngLatToScreen(lon, lat, width, height));
            for (let i = 0; i < ringPts.length; i++) {
              const [x, y] = ringPts[i];
              if (i === 0) ctx.moveTo(x, y);
              else ctx.lineTo(x, y);
            }
            ctx.closePath();
          }
          if (fillStyle) {
            ctx.fillStyle = fillStyle;
            ctx.fill("evenodd");
          }
          if (strokeStyle) {
            if (dash) ctx.setLineDash(dash);
            ctx.strokeStyle = strokeStyle;
            ctx.lineWidth = lineWidth;
            ctx.stroke();
            if (dash) ctx.setLineDash([]);
          }
        }
      }
      return minX < Infinity ? { minX, minY, maxX, maxY } : null;
    };

    const iso = this.isolatedBoundary;
    const isoGeom = iso?.feature?.geometry || null;
    const isoBBox = iso?.bbox || null;
    const isoMode = iso?.mode || "contents";
    const isLightBg = state.basemap === "solid_light" || state.basemap === "warm_parchment";

    const isPointInIsolated = (lng, lat) => {
      if (!isoGeom) return true;
      if (isoBBox) {
        if (lng < isoBBox[0] || lng > isoBBox[2] || lat < isoBBox[1] || lat > isoBBox[3]) {
          return false;
        }
      }
      return this._pointInPolygonGeometry(lng, lat, isoGeom);
    };

    const isPolygonInIsolated = (geom) => {
      if (!isoGeom) return true;
      if (!geom || !geom.coordinates) return false;
      const ring =
        geom.type === "Polygon"
          ? geom.coordinates[0]
          : geom.type === "MultiPolygon" && geom.coordinates[0]
          ? geom.coordinates[0][0]
          : null;
      if (!ring || !ring.length) return false;
      let sumLng = 0;
      let sumLat = 0;
      let n = 0;
      const step = Math.max(1, Math.floor(ring.length / 6));
      for (let i = 0; i < ring.length; i += step) {
        const pt = ring[i];
        if (Array.isArray(pt) && Number.isFinite(pt[0]) && Number.isFinite(pt[1])) {
          sumLng += pt[0];
          sumLat += pt[1];
          n += 1;
        }
      }
      if (n === 0) return false;
      return isPointInIsolated(sumLng / n, sumLat / n);
    };

    // If a polygon is isolated over a slippy basemap, dim everything outside the isolated polygon
    if (isoGeom && !isNoMap) {
      const polys = isoGeom.type === "Polygon" ? [isoGeom.coordinates] : isoGeom.coordinates || [];
      ctx.save();
      ctx.beginPath();
      ctx.rect(0, 0, width, height);
      for (const poly of polys) {
        if (!Array.isArray(poly) || !poly[0] || poly[0].length < 3) continue;
        const extRing = poly[0];
        for (let i = 0; i < extRing.length; i++) {
          const [sx, sy] = this._lngLatToScreen(extRing[i][0], extRing[i][1], width, height);
          if (i === 0) ctx.moveTo(sx, sy);
          else ctx.lineTo(sx, sy);
        }
        ctx.closePath();
      }
      ctx.fillStyle = isLightBg ? "rgba(244, 241, 234, 0.88)" : "rgba(13, 17, 23, 0.88)";
      ctx.fill("evenodd");
      ctx.restore();
    }

    if (!isoGeom) {
      // 2. Annexation History Overlay
      if (Boolean(state.layers?.annexations) && this.overlaysData?.annexations) {
        const activeDecade = resolveActiveAnnexationDecade(state);
        for (const feat of this.overlaysData.annexations.features || []) {
          const dec = Number(feat.properties?.decade) || 1836;
          if (activeDecade !== null && dec !== activeDecade) continue;
          if (state.showAnnexationSpokes === false && feat.properties?.annex_subtype === "spoke_or_spa") {
            continue;
          }
          drawPolygonFeature(
            feat.geometry,
            "rgba(217, 119, 6, 0.08)",
            "rgba(245, 158, 11, 0.78)",
            1.8,
            [5, 4]
          );
        }
      }

      // 3. NRHP, Heritage, COH Historic, Potential Historic, and Cultural Districts
      if (state.layers.culturalDistricts && this.overlaysData?.cultural_districts) {
        for (const feat of this.overlaysData.cultural_districts.features || []) {
          drawPolygonFeature(
            feat.geometry,
            "rgba(236, 72, 153, 0.06)",
            "#F472B6",
            2.0,
            [6, 3]
          );
        }
      }
      if (
        state.layers.potentialHistoricDistricts &&
        this.overlaysData?.potential_historic_districts
      ) {
        for (const feat of this.overlaysData.potential_historic_districts.features || []) {
          const alreadyCoh = Boolean(feat.properties?.already_designated_coh_hd);
          drawPolygonFeature(
            feat.geometry,
            alreadyCoh ? "rgba(56, 189, 248, 0.08)" : "rgba(251, 146, 60, 0.09)",
            alreadyCoh ? "#38BDF8" : "#FB923C",
            2.1,
            [6, 3, 2, 3]
          );
        }
      }
      if (state.colorMode === "preservation_status") {
        for (const feat of this.overlaysData?.land_use_protections?.features || []) {
          drawPolygonFeature(
            feat.geometry,
            "rgba(56, 189, 248, 0.12)",
            "rgba(56, 189, 248, 0.78)",
            1.2,
            [3, 3]
          );
        }
        for (const feat of this.overlaysData?.platted_subdivisions?.features || []) {
          if (!feat.properties?.has_deed_docs) continue;
          drawPolygonFeature(
            feat.geometry,
            "rgba(56, 189, 248, 0.12)",
            "rgba(56, 189, 248, 0.78)",
            1.2,
            [3, 3]
          );
        }
      }
      if (state.layers.nrhpDistricts && this.overlaysData?.nrhp_districts) {
        for (const feat of this.overlaysData.nrhp_districts.features || []) {
          drawPolygonFeature(
            feat.geometry,
            "rgba(168, 85, 247, 0.07)",
            "#C084FC",
            1.8,
            [4, 3]
          );
        }
      }
      if (state.layers.heritageDistricts && this.overlaysData?.heritage_districts) {
        for (const feat of this.overlaysData.heritage_districts.features || []) {
          drawPolygonFeature(feat.geometry, "rgba(16, 185, 129, 0.09)", "#34D399", 2.2);
        }
      }
      if (state.layers.historicDistricts && this.overlaysData?.historic_districts) {
        for (const feat of this.overlaysData.historic_districts.features || []) {
          drawPolygonFeature(feat.geometry, "rgba(56, 189, 248, 0.06)", "#38BDF8", 2.0);
        }
      }

      // 3b. Historic Wards (1839-1905), 1937 HOLC Security Zones (47), COH Super Neighborhoods (88), and Neighborhoods (1,387)
      if (state.layers.holc1937 && this.overlaysData?.holc_1937_zones) {
        const activeHolcGrade = String(state.holcGradeFilter || "all").trim().toUpperCase();
        for (const feat of this.overlaysData.holc_1937_zones.features || []) {
          const p = feat.properties || {};
          const g = String(p.holc_grade || "").toUpperCase();
          if (activeHolcGrade !== "ALL" && g !== activeHolcGrade) continue;
          const fill =
            g === "A"
              ? "rgba(34, 197, 94, 0.16)"
              : g === "B"
              ? "rgba(56, 189, 248, 0.16)"
              : g === "C"
              ? "rgba(250, 204, 21, 0.16)"
              : "rgba(239, 68, 68, 0.18)";
          const stroke =
            g === "A"
              ? "#4ADE80"
              : g === "B"
              ? "#7DD3FC"
              : g === "C"
              ? "#FDE047"
              : "#F87171";
          drawPolygonFeature(feat.geometry, fill, stroke, 2.2);
        }
      }
      if (state.layers.historicWards && this.overlaysData?.historic_wards) {
        const targetEra = resolveActiveWardEra(state);
        for (const feat of this.overlaysData.historic_wards.features || []) {
          const p = feat.properties || {};
          if (Number(p.era || p.ward_era || p.era_year) !== targetEra) continue;
          drawPolygonFeature(
            feat.geometry,
            p.color || "rgba(230, 57, 70, 0.08)",
            p.color || "#F59E0B",
            2.2,
            [6, 3]
          );
        }
      }
      if (state.layers.superNeighborhoods && this.overlaysData?.super_neighborhoods) {
        for (const feat of this.overlaysData.super_neighborhoods.features || []) {
          drawPolygonFeature(
            feat.geometry,
            "rgba(96, 165, 250, 0.05)",
            "rgba(96, 165, 250, 0.72)",
            1.8,
            [4, 2]
          );
        }
      }
      if (state.layers.neighborhoods && this.overlaysData?.neighborhoods) {
        for (const feat of this.overlaysData.neighborhoods.features || []) {
          drawPolygonFeature(
            feat.geometry,
            "rgba(45, 212, 191, 0.06)",
            "rgba(45, 212, 191, 0.72)",
            1.3
          );
        }
      }
      if (state.layers.plattedSubdivisions && this.overlaysData?.platted_subdivisions) {
        for (const feat of this.overlaysData.platted_subdivisions.features || []) {
          const hasDocs = Boolean(feat.properties?.has_deed_docs);
          drawPolygonFeature(
            feat.geometry,
            hasDocs ? "rgba(245, 158, 11, 0.14)" : "rgba(34, 211, 238, 0.08)",
            hasDocs ? "#FBBF24" : "rgba(34, 211, 238, 0.85)",
            hasDocs ? 2.2 : 1.35,
            [2, 1.5]
          );
        }
      }
      if (state.layers.landUseProtections && this.overlaysData?.land_use_protections) {
        for (const feat of this.overlaysData.land_use_protections.features || []) {
          const pt = feat.properties?.protection_type;
          const fill =
            pt === "smbl"
              ? "rgba(16, 185, 129, 0.13)"
              : pt === "conservation"
              ? "rgba(244, 63, 94, 0.14)"
              : "rgba(245, 158, 11, 0.13)";
          const stroke =
            pt === "smbl"
              ? "#34D399"
              : pt === "conservation"
              ? "#FB7185"
              : "#FBBF24";
          drawPolygonFeature(feat.geometry, fill, stroke, 1.7, [2, 1.5]);
        }
      }
      const drawPolylineFeature = (geom, strokeStyle, lineWidth = 2.0, dash = null) => {
        if (!geom || !geom.coordinates) return;
        const lines =
          geom.type === "LineString"
            ? [geom.coordinates]
            : geom.type === "MultiLineString"
            ? geom.coordinates
            : [];
        for (const coords of lines) {
          if (!Array.isArray(coords) || coords.length < 2) continue;
          ctx.beginPath();
          for (let i = 0; i < coords.length; i++) {
            const [sx, sy] = this._lngLatToScreen(coords[i][0], coords[i][1], width, height);
            if (i === 0) ctx.moveTo(sx, sy);
            else ctx.lineTo(sx, sy);
          }
          if (dash) ctx.setLineDash(dash);
          ctx.strokeStyle = strokeStyle;
          ctx.lineWidth = lineWidth;
          ctx.lineCap = "round";
          ctx.lineJoin = "round";
          ctx.stroke();
          if (dash) ctx.setLineDash([]);
        }
      };

      if (state.layers.historicalWaterways && this.overlaysData?.historical_waterways) {
        for (const feat of this.overlaysData.historical_waterways.features || []) {
          const wt = feat.properties?.waterway_type;
          const color =
            wt === "bayou"
              ? "#0EA5E9"
              : wt === "buried_gully"
              ? "#FBBF24"
              : wt === "historic_oxbow"
              ? "#A78BFA"
              : "#22D3EE";
          const lw = wt === "bayou" ? 3.0 : wt === "buried_gully" || wt === "historic_oxbow" ? 2.5 : 2.0;
          const dash = wt === "buried_gully" ? [5, 3] : wt === "historic_oxbow" ? [4, 4] : null;
          drawPolylineFeature(feat.geometry, color, lw, dash);
        }
      }
      if (state.layers.historicalRailroads && this.overlaysData?.historical_railroads) {
        for (const feat of this.overlaysData.historical_railroads.features || []) {
          const rt = feat.properties?.rail_type;
          if (rt === "depot" && feat.geometry?.type === "Point") {
            const [sx, sy] = this._lngLatToScreen(
              feat.geometry.coordinates[0],
              feat.geometry.coordinates[1],
              width,
              height
            );
            ctx.beginPath();
            ctx.arc(sx, sy, 5.8, 0, Math.PI * 2);
            ctx.fillStyle = "#FDE047";
            ctx.fill();
            ctx.strokeStyle = "#0F172A";
            ctx.lineWidth = 2.0;
            ctx.stroke();
            continue;
          }
          const color =
            rt === "mainline"
              ? "#F59E0B"
              : rt === "abandoned_trail"
              ? "#FB7185"
              : rt === "streetcar_interurban"
              ? "#C084FC"
              : "#94A3B8";
          const lw = rt === "mainline" ? 2.6 : rt === "abandoned_trail" ? 2.3 : 2.0;
          const dash = rt === "abandoned_trail" ? [5, 3] : rt === "streetcar_interurban" ? [3, 2.5] : null;
          drawPolylineFeature(feat.geometry, color, lw, dash);
        }
      }
      if (this.selectedBoundaryFeature && this.selectedBoundaryFeature.geometry) {
        const sGeom = this.selectedBoundaryFeature.geometry;
        if (sGeom.type === "LineString" || sGeom.type === "MultiLineString") {
          drawPolylineFeature(sGeom, isLightBg ? "#0F172A" : "#FDE047", 3.6, [4, 3]);
        } else if (sGeom.type === "Point" && Array.isArray(sGeom.coordinates)) {
          const [sx, sy] = this._lngLatToScreen(sGeom.coordinates[0], sGeom.coordinates[1], width, height);
          ctx.beginPath();
          ctx.arc(sx, sy, 8.5, 0, Math.PI * 2);
          ctx.fillStyle = "#FDE047";
          ctx.fill();
          ctx.strokeStyle = "#0F172A";
          ctx.lineWidth = 2.4;
          ctx.stroke();
        } else {
          drawPolygonFeature(
            sGeom,
            "rgba(253, 224, 71, 0.08)",
            isLightBg ? "#0F172A" : "#FDE047",
            2.6,
            [3, 2]
          );
        }
      }
    } else if (isoMode !== "footprints_only") {
      // Draw the isolated boundary polygon outline (and subtle silhouette fill in border_only mode)
      const isoFill =
        isoMode === "border_only"
          ? isLightBg
            ? "rgba(15, 23, 42, 0.10)"
            : "rgba(149, 201, 89, 0.14)"
          : isLightBg
          ? "rgba(15, 23, 42, 0.03)"
          : "rgba(253, 224, 71, 0.04)";
      const isoStroke =
        isoMode === "border_only"
          ? isLightBg
            ? "#0F172A"
            : "#95C959"
          : isLightBg
          ? "#0F172A"
          : "#FDE047";
      const isoLw = isoMode === "border_only" ? 3.4 : 2.6;
      drawPolygonFeature(
        isoGeom,
        isoFill,
        isoStroke,
        isoLw,
        isoMode === "border_only" ? null : [4, 2.5]
      );
    }

    cs.renderedBBoxes = [];

    // 4. Tax Parcels Layer
    const allowStructures = !isoGeom || isoMode !== "border_only";
    const showBuildings =
      allowStructures && (state.renderMode === "buildings" || state.renderMode === "both");
    const showParcelsFill = allowStructures && state.renderMode === "parcels";
    const showParcelsLine =
      allowStructures && (state.renderMode === "both" || state.renderMode === "parcels");

    if (showParcelsFill || showParcelsLine) {
      for (const feat of this.parcelsData) {
        const p = feat.properties || {};
        if (!featureMatchesFilter(p, state)) continue;
        if (isoGeom && !isPolygonInIsolated(feat.geometry)) continue;
        const fill = showParcelsFill
          ? evaluateFeatureColor(p, state.colorMode, state.paletteStyle)
          : null;
        const stroke = showParcelsLine ? "rgba(148, 163, 184, 0.42)" : null;
        const bbox = drawPolygonFeature(feat.geometry, fill, stroke, 0.7);
        if (bbox && showParcelsFill) {
          cs.renderedBBoxes.push({ ...bbox, props: p, geom: feat.geometry });
        }
      }
    }

    // 5. Building Footprints & Curated Overrides (2D or 3D Isometric Extrusion)
    if (showBuildings) {
      const seenCanvasIds = new Set();
      const seenCanvasHcads = new Set();
      const selHcad = String(this.selectedFeatureProps?.hcad_num || "").trim();
      const allBuildingFeatures = [
        ...(this.overridesFC?.features || []),
        ...this.buildingsData,
      ];
      for (const feat of allBuildingFeatures) {
        const rawP = feat.properties || {};
        if (rawP.suppress_only) continue;
        const p = applyOverrideToProperties(rawP, this.curatedOverrides);
        const fid = String(p.id || p.building_id || "").trim();
        const hcad = String(p.hcad_num || "").trim();
        const isCompositeId = fid.includes("#");
        if (fid) {
          if (seenCanvasIds.has(fid)) continue;
          seenCanvasIds.add(fid);
        }
        if (hcad && !isCompositeId) {
          if (seenCanvasHcads.has(hcad)) continue;
          seenCanvasHcads.add(hcad);
        }
        if (!featureMatchesFilter(p, state)) continue;
        if (isoGeom && !isPolygonInIsolated(feat.geometry)) continue;
        const color = evaluateFeatureColor(p, state.colorMode, state.paletteStyle);
        const isSelected = Boolean(
          (this.selectedFeatureId &&
            (p.id === this.selectedFeatureId || p.building_id === this.selectedFeatureId)) ||
            (selHcad && !isCompositeId && hcad === selHcad)
        );
        const stroke = isSelected ? "#FDE047" : "rgba(15, 17, 21, 0.72)";
        const lw = isSelected ? 2.8 : 0.75;
        const extrudeM = state.extrude3D ? Number(p.height_m) || 5.0 : 0;

        const bbox = drawPolygonFeature(feat.geometry, color, stroke, lw, null, extrudeM);
        if (bbox) {
          cs.renderedBBoxes.push({ ...bbox, props: p, geom: feat.geometry });
        }
      }
    }

    // 5b. Lost Houston Demolished Historic Structures (Sanborn Underwriter Color Key & Dashed Blueprint Outline)
    const showLostHouston =
      !isoGeom &&
      (Boolean(state.layers?.lostHouston) || Boolean(state.historicalSnapshotMode));
    const visibleLostHoustonFeatures = [];
    if (showLostHouston && this.overlaysData?.lost_houston) {
      const minY = Number(state.minYear ?? 1836) || 1836;
      const maxY = Number(state.maxYear ?? 2026) || 2026;
      for (const feat of this.overlaysData.lost_houston.features || []) {
        const p = feat.properties || {};
        const yb = Number(p.year_built) || 1836;
        const yd = Number(p.year_demolished) || 1950;
        if (state.historicalSnapshotMode) {
          if (!(yb <= maxY && yd > maxY && yb >= minY)) continue;
        } else if (minY > 1836 || maxY < 2026) {
          if (!(yb <= maxY && yd >= minY)) continue;
        }
        visibleLostHoustonFeatures.push(feat);
        const mat = getSanbornMaterialColors(p.sanborn_material);
        const extrudeM = state.extrude3D ? (Number(p.height_m) || 10.0) * 0.55 : 0;
        const bbox = drawPolygonFeature(
          feat.geometry,
          mat.fillColor,
          mat.strokeColor,
          2.2,
          [5, 3],
          extrudeM
        );
        if (bbox) {
          cs.renderedBBoxes.push({
            ...bbox,
            props: { ...p, overlay_layer: "lost_houston", is_boundary_feature: true },
            geom: feat.geometry,
          });
        }
      }
    }

    const allowPointMarkers = !isoGeom || isoMode === "contents";

    // 6. THC Markers
    if (allowPointMarkers && state.layers.thcMarkers && this.overlaysData?.thc_markers) {
      for (const feat of this.overlaysData.thc_markers.features || []) {
        const coords = feat.geometry?.coordinates;
        if (!coords) continue;
        if (isoGeom && !isPointInIsolated(coords[0], coords[1])) continue;
        const [sx, sy] = this._lngLatToScreen(coords[0], coords[1], width, height);
        if (sx < 0 || sx > width || sy < 0 || sy > height) continue;
        ctx.beginPath();
        ctx.arc(sx, sy, 4.8, 0, Math.PI * 2);
        ctx.fillStyle = "#C084FC";
        ctx.fill();
        ctx.lineWidth = 1.4;
        ctx.strokeStyle = "#1E1B4B";
        ctx.stroke();
        cs.renderedBBoxes.push({
          minX: sx - 6,
          minY: sy - 6,
          maxX: sx + 6,
          maxY: sy + 6,
          props: feat.properties,
          geom: feat.geometry,
        });
      }
    }

    // 6b. National Register (NRHP) Individual Properties (238)
    if (allowPointMarkers && state.layers.nrhpProperties && this.overlaysData?.nrhp_properties) {
      for (const feat of this.overlaysData.nrhp_properties.features || []) {
        const coords = feat.geometry?.coordinates;
        if (!coords) continue;
        if (isoGeom && !isPointInIsolated(coords[0], coords[1])) continue;
        const [sx, sy] = this._lngLatToScreen(coords[0], coords[1], width, height);
        if (sx < 0 || sx > width || sy < 0 || sy > height) continue;
        const isGone = Boolean(feat.properties?.is_demolished_nrhp);
        if (!isGone) {
          ctx.beginPath();
          ctx.arc(sx, sy, 8.8, 0, Math.PI * 2);
          ctx.fillStyle = "rgba(168, 85, 247, 0.28)";
          ctx.fill();
        }
        ctx.beginPath();
        ctx.arc(sx, sy, isGone ? 4.8 : 5.8, 0, Math.PI * 2);
        ctx.fillStyle = isGone ? "#475569" : "#A855F7";
        ctx.fill();
        ctx.lineWidth = isGone ? 1.4 : 1.8;
        ctx.strokeStyle = isGone ? "#CBD5E1" : "#FAF5FF";
        ctx.stroke();
        cs.renderedBBoxes.push({
          minX: sx - 8,
          minY: sy - 8,
          maxX: sx + 8,
          maxY: sy + 8,
          props: feat.properties,
          geom: feat.geometry,
        });
      }
    }

    // 6c. 1915 Red Book of Houston (970 African American Heritage Sites)
    if (allowPointMarkers && state.layers.redBook1915 && this.overlaysData?.red_book_1915) {
      const rbCatFilter = String(state.redBookCategory || "all").trim().toLowerCase();
      for (const feat of this.overlaysData.red_book_1915.features || []) {
        const rp = feat.properties || {};
        if (rbCatFilter !== "all" && String(rp.category || "").toLowerCase() !== rbCatFilter) {
          continue;
        }
        const coords = feat.geometry?.coordinates;
        if (!coords) continue;
        if (isoGeom && !isPointInIsolated(coords[0], coords[1])) continue;
        const [sx, sy] = this._lngLatToScreen(coords[0], coords[1], width, height);
        if (sx < 0 || sx > width || sy < 0 || sy > height) continue;
        const cat = String(rp.category || "residence");
        const catColor =
          cat === "church"
            ? "#F59E0B"
            : cat === "school"
            ? "#38BDF8"
            : cat === "business"
            ? "#FB7185"
            : "#C084FC";
        const isSurviving = rp.survival_status === "surviving_era_structure";
        const isCleared = rp.survival_status === "cleared_or_row";
        if (isSurviving) {
          ctx.beginPath();
          ctx.arc(sx, sy, 9.8, 0, Math.PI * 2);
          ctx.fillStyle = "rgba(245, 158, 11, 0.28)";
          ctx.fill();
        }
        ctx.beginPath();
        ctx.arc(sx, sy, isSurviving ? 6.2 : isCleared ? 4.7 : 5.3, 0, Math.PI * 2);
        ctx.fillStyle = isCleared ? "#334155" : catColor;
        ctx.fill();
        ctx.lineWidth = isSurviving ? 2.1 : 1.6;
        ctx.strokeStyle = isSurviving ? "#FEF3C7" : isCleared ? catColor : "#0F172A";
        ctx.stroke();
        cs.renderedBBoxes.push({
          minX: sx - 8,
          minY: sy - 8,
          maxX: sx + 8,
          maxY: sy + 8,
          props: { ...rp, overlay_layer: "red_book_1915", is_boundary_feature: true },
          geom: feat.geometry,
        });
      }
    }

    // 6d. HAHC COA & Designation Tracker (283 Parcels / 403 Filings)
    if (allowPointMarkers && state.layers.hahcTracker && this.overlaysData?.hahc_coa_filings) {
      const hf = String(state.hahcFilter || "all").trim().toLowerCase();
      for (const feat of this.overlaysData.hahc_coa_filings.features || []) {
        const hp = feat.properties || {};
        if (hf === "active_agenda" && !hp.has_active_agenda) continue;
        if (hf === "demolition_relocation" && !hp.has_demolition_relocation) continue;
        if (hf === "designation" && !hp.has_designation) continue;
        if (hf === "denied_or_cor" && !hp.has_denied_or_cor) continue;
        if (hf === "addition_new" && (hp.has_demolition_relocation || hp.has_designation)) continue;
        const coords = feat.geometry?.coordinates;
        if (!coords) continue;
        if (isoGeom && !isPointInIsolated(coords[0], coords[1])) continue;
        const [sx, sy] = this._lngLatToScreen(coords[0], coords[1], width, height);
        if (sx < 0 || sx > width || sy < 0 || sy > height) continue;
        const cColor = hp.has_active_agenda
          ? "#F43F5E"
          : hp.has_demolition_relocation
          ? "#EF4444"
          : hp.has_designation
          ? "#A855F7"
          : hp.has_denied_or_cor
          ? "#F59E0B"
          : "#14B8A6";
        ctx.beginPath();
        ctx.arc(sx, sy, hp.has_active_agenda ? 10.5 : 8.5, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(20, 184, 166, 0.22)";
        ctx.fill();
        ctx.beginPath();
        ctx.arc(sx, sy, hp.has_active_agenda ? 6.4 : 5.4, 0, Math.PI * 2);
        ctx.fillStyle = cColor;
        ctx.fill();
        ctx.lineWidth = hp.has_active_agenda ? 2.1 : 1.7;
        ctx.strokeStyle = hp.has_active_agenda ? "#FFE4E6" : "#0F172A";
        ctx.stroke();
        cs.renderedBBoxes.push({
          minX: sx - 8,
          minY: sy - 8,
          maxX: sx + 8,
          maxY: sy + 8,
          props: { ...hp, overlay_layer: "hahc_coa_filings", is_boundary_feature: true },
          geom: feat.geometry,
        });
      }
    }

    // 6e. Architect / Builder / Style Map Portfolio Works
    if (Array.isArray(this.portfolioWorks) && this.portfolioWorks.length > 0) {
      const showNums = this.portfolioWorks.length <= 60;
      for (let i = 0; i < this.portfolioWorks.length; i++) {
        const w = this.portfolioWorks[i];
        if (!Number.isFinite(w.lng) || !Number.isFinite(w.lat)) continue;
        const [sx, sy] = this._lngLatToScreen(w.lng, w.lat, width, height);
        if (sx < 0 || sx > width || sy < 0 || sy > height) continue;
        const isAct = i === this.activePortfolioWorkIndex;
        const outerR = showNums ? (isAct ? 16 : 13.5) : isAct ? 12.5 : 9.2;
        const innerR = showNums ? (isAct ? 11.5 : 9.8) : isAct ? 7.6 : 6.0;
        ctx.beginPath();
        ctx.arc(sx, sy, outerR, 0, Math.PI * 2);
        ctx.fillStyle = w.isLost ? "rgba(244, 63, 94, 0.34)" : "rgba(251, 191, 36, 0.34)";
        ctx.fill();
        ctx.beginPath();
        ctx.arc(sx, sy, innerR, 0, Math.PI * 2);
        ctx.fillStyle = w.isLost ? "#BE123C" : "#D97706";
        ctx.fill();
        ctx.lineWidth = isAct ? 2.4 : 1.8;
        ctx.strokeStyle = isAct ? "#FFFFFF" : w.isLost ? "#FECDD3" : "#FEF3C7";
        ctx.stroke();
        if (showNums) {
          ctx.save();
          ctx.fillStyle = "#FFFFFF";
          ctx.font = "700 9.5px ui-monospace, SFMono-Regular, Menlo, monospace";
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";
          ctx.fillText(w.isLost ? `†${i + 1}` : String(i + 1), sx, sy + 0.5);
          ctx.restore();
        }
        cs.renderedBBoxes.push({
          minX: sx - innerR - 2,
          minY: sy - innerR - 2,
          maxX: sx + innerR + 2,
          maxY: sy + innerR + 2,
          props: {
            id: w.id,
            name: w.title,
            address: w.address,
            year_built: w.yearBuilt,
            year_demolished: w.yearDemolished || 0,
            is_lost: Boolean(w.isLost),
            portfolio_index: i,
            overlay_layer: "portfolio_work",
            is_boundary_feature: true,
          },
          geom: { type: "Point", coordinates: [w.lng, w.lat] },
        });
      }
    }

    // 7. COH Designated Landmarks (PLM, LM in HD, and 90-Day Waiver Vulnerable LM)
    if (allowPointMarkers && state.layers.landmarks && this.overlaysData?.landmarks) {
      const lmMode = String(state.landmarkFilter || "all");
      for (const feat of this.overlaysData.landmarks.features || []) {
        const lp = feat.properties || {};
        const tier = String(lp.protection_tier || "");
        if (lmMode === "protected" && tier !== "plm" && tier !== "lm_in_hd") continue;
        if (lmMode === "waiver_lm" && tier !== "lm_waiver_vulnerable") continue;
        const coords = feat.geometry?.coordinates;
        if (!coords) continue;
        if (isoGeom && !isPointInIsolated(coords[0], coords[1])) continue;
        const [sx, sy] = this._lngLatToScreen(coords[0], coords[1], width, height);
        if (sx < 0 || sx > width || sy < 0 || sy > height) continue;
        const isWaiver = tier === "lm_waiver_vulnerable";
        const isLmInHd = tier === "lm_in_hd";
        if (isWaiver) {
          ctx.beginPath();
          ctx.arc(sx, sy, 9.2, 0, Math.PI * 2);
          ctx.lineWidth = 1.4;
          ctx.strokeStyle = "rgba(251, 191, 36, 0.88)";
          ctx.stroke();
        }
        ctx.beginPath();
        ctx.arc(sx, sy, 5.8, 0, Math.PI * 2);
        ctx.fillStyle = isWaiver ? "#F59E0B" : "#E63946";
        ctx.fill();
        ctx.lineWidth = isLmInHd ? 2.0 : 1.6;
        ctx.strokeStyle = isLmInHd ? "#2DD4BF" : isWaiver ? "#0F172A" : "#FFFBEB";
        ctx.stroke();
        cs.renderedBBoxes.push({
          minX: sx - 8,
          minY: sy - 8,
          maxX: sx + 8,
          maxY: sy + 8,
          props: feat.properties,
          geom: feat.geometry,
        });
      }
    }

    // 8. Preservation Houston Good Brick Award Winners (1979–2026)
    if (allowPointMarkers && state.layers.goodBrickAwards && this.overlaysData?.good_brick_awards) {
      for (const feat of this.overlaysData.good_brick_awards.features || []) {
        const coords = feat.geometry?.coordinates;
        if (!coords) continue;
        if (isoGeom && !isPointInIsolated(coords[0], coords[1])) continue;
        const [sx, sy] = this._lngLatToScreen(coords[0], coords[1], width, height);
        if (sx < 0 || sx > width || sy < 0 || sy > height) continue;
        const multiAward = Number(feat.properties?.good_brick_count || 1) > 1;
        const r = multiAward ? 6.8 : 5.6;
        // Outer PH green halo
        ctx.beginPath();
        ctx.arc(sx, sy, r + 3.2, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(149, 201, 89, 0.32)";
        ctx.fill();
        // Inner PH green badge
        ctx.beginPath();
        ctx.arc(sx, sy, r, 0, Math.PI * 2);
        ctx.fillStyle = "#95C959";
        ctx.fill();
        ctx.lineWidth = 1.8;
        ctx.strokeStyle = "#F4F9EE";
        ctx.stroke();
        cs.renderedBBoxes.push({
          minX: sx - 8,
          minY: sy - 8,
          maxX: sx + 8,
          maxY: sy + 8,
          props: feat.properties,
          geom: feat.geometry,
        });
      }
    }

    // 8a. Relocated Structures & Historic Contractor Portfolio Sites
    if (allowPointMarkers && state.layers.relocatedStructures && this.overlaysData?.relocated_structures) {
      for (const feat of this.overlaysData.relocated_structures.features || []) {
        const coords = feat.geometry?.coordinates;
        if (!coords) continue;
        if (isoGeom && !isPointInIsolated(coords[0], coords[1])) continue;
        const [sx, sy] = this._lngLatToScreen(coords[0], coords[1], width, height);
        if (sx < 0 || sx > width || sy < 0 || sy > height) continue;
        const isReloc = Boolean(feat.properties?.was_relocated);
        const dotColor = isReloc ? "#38BDF8" : "#F59E0B";
        ctx.beginPath();
        ctx.arc(sx, sy, 8.8, 0, Math.PI * 2);
        ctx.fillStyle = isReloc ? "rgba(56, 189, 248, 0.32)" : "rgba(245, 158, 11, 0.32)";
        ctx.fill();
        ctx.beginPath();
        ctx.arc(sx, sy, 5.6, 0, Math.PI * 2);
        ctx.fillStyle = dotColor;
        ctx.fill();
        ctx.lineWidth = 1.8;
        ctx.strokeStyle = "#0F172A";
        ctx.stroke();
        cs.renderedBBoxes.push({
          minX: sx - 8,
          minY: sy - 8,
          maxX: sx + 8,
          maxY: sy + 8,
          props: feat.properties,
          geom: feat.geometry,
        });
      }
    }

    // 8a2. Active Permit & Demolition Watch Alerts
    if (allowPointMarkers && state.layers.permitWatch && this.permitAlertsFC?.features) {
      for (const feat of this.permitAlertsFC.features) {
        const coords = feat.geometry?.coordinates;
        if (!coords) continue;
        if (isoGeom && !isPointInIsolated(coords[0], coords[1])) continue;
        const [sx, sy] = this._lngLatToScreen(coords[0], coords[1], width, height);
        if (sx < 0 || sx > width || sy < 0 || sy > height) continue;
        const isCritical = feat.properties?.severity === "critical";
        const dotColor = isCritical ? "#EF4444" : "#F59E0B";
        const ringColor = isCritical ? "#FCA5A5" : "#FDE68A";
        const glowColor = isCritical ? "rgba(239, 68, 68, 0.34)" : "rgba(245, 158, 11, 0.28)";
        ctx.beginPath();
        ctx.arc(sx, sy, isCritical ? 14.0 : 11.2, 0, Math.PI * 2);
        ctx.fillStyle = glowColor;
        ctx.fill();
        ctx.beginPath();
        ctx.arc(sx, sy, isCritical ? 9.8 : 8.2, 0, Math.PI * 2);
        ctx.lineWidth = 1.6;
        ctx.strokeStyle = ringColor;
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(sx, sy, isCritical ? 6.6 : 5.6, 0, Math.PI * 2);
        ctx.fillStyle = dotColor;
        ctx.fill();
        ctx.lineWidth = 2.0;
        ctx.strokeStyle = "#FEF2F2";
        ctx.stroke();
        cs.renderedBBoxes.push({
          minX: sx - 10,
          minY: sy - 10,
          maxX: sx + 10,
          maxY: sy + 10,
          props: feat.properties,
          geom: feat.geometry,
        });
      }
    }

    // 8a3. Planning Commission Plats & Variances
    if (allowPointMarkers && state.layers.platWatch && this.platWatchFC?.features) {
      for (const feat of this.platWatchFC.features) {
        const coords = feat.geometry?.coordinates;
        if (!coords) continue;
        if (isoGeom && !isPointInIsolated(coords[0], coords[1])) continue;
        const [sx, sy] = this._lngLatToScreen(coords[0], coords[1], width, height);
        if (sx < 0 || sx > width || sy < 0 || sy > height) continue;
        const isCritical = feat.properties?.severity === "critical";
        const dotColor = isCritical ? "#F43F5E" : "#8B5CF6";
        const ringColor = isCritical ? "#FDA4AF" : "#C4B5FD";
        const glowColor = isCritical ? "rgba(244, 63, 94, 0.30)" : "rgba(139, 92, 246, 0.26)";
        ctx.beginPath();
        ctx.arc(sx, sy, isCritical ? 12.5 : 10.0, 0, Math.PI * 2);
        ctx.fillStyle = glowColor;
        ctx.fill();
        ctx.beginPath();
        ctx.arc(sx, sy, isCritical ? 9.0 : 7.4, 0, Math.PI * 2);
        ctx.lineWidth = 1.5;
        ctx.strokeStyle = ringColor;
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(sx, sy, isCritical ? 6.0 : 5.0, 0, Math.PI * 2);
        ctx.fillStyle = dotColor;
        ctx.fill();
        ctx.lineWidth = 1.8;
        ctx.strokeStyle = "#FFF1F2";
        ctx.stroke();
        cs.renderedBBoxes.push({
          minX: sx - 9,
          minY: sy - 9,
          maxX: sx + 9,
          maxY: sy + 9,
          props: feat.properties,
          geom: feat.geometry,
        });
      }
    }

    // 8b. Boundary Labels in 2D Canvas Mode
    const placedBoxes = [];
    const drawBoundaryLabels = (features, minZ, textColor) => {
      if (isoGeom || cs.zoom < minZ || !Array.isArray(features)) return;
      ctx.save();
      ctx.font = "700 10.5px Inter, system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      const ordered = [...features].sort(
        (a, b) =>
          (b?.properties?.has_deed_docs ? 1 : 0) - (a?.properties?.has_deed_docs ? 1 : 0)
      );
      for (const feat of ordered) {
        const p = feat.properties || {};
        const lng = Number(p.label_lng);
        const lat = Number(p.label_lat);
        const rawName = String(p.name || "").trim();
        const hasDocs = Boolean(p.has_deed_docs);
        const cleanLostName = rawName.replace(/\s*\(\d{4}(?:\s*[/–-]\s*\d{4})?\)\s*$/, "").trim();
        const name =
          p.overlay_layer === "lost_houston"
            ? `† ${cleanLostName} (${p.year_built}-${p.year_demolished})`
            : hasDocs
            ? `📜 ${rawName}`
            : rawName;
        if (!rawName || !Number.isFinite(lng) || !Number.isFinite(lat)) continue;
        if (lng < west || lng > east || lat < south || lat > north) continue;
        const [sx, sy] = this._lngLatToScreen(lng, lat, width, height);
        if (sx < 30 || sx > width - 30 || sy < 20 || sy > height - 20) continue;
        const tw = Math.min(180, name.length * 6.5 + 18);
        const th = 22;
        const overlaps = placedBoxes.some(
          (b) =>
            Math.abs(b.x - sx) < (b.w + tw) * 0.58 &&
            Math.abs(b.y - sy) < (b.h + th) * 0.72
        );
        if (overlaps) continue;
        placedBoxes.push({ x: sx, y: sy, w: tw, h: th });
        ctx.lineWidth = 3.2;
        ctx.strokeStyle = "rgba(11, 15, 23, 0.92)";
        ctx.strokeText(name, sx, sy);
        ctx.fillStyle = hasDocs ? "#FDE68A" : textColor;
        ctx.fillText(name, sx, sy);
      }
      ctx.restore();
    };
    if (showLostHouston && visibleLostHoustonFeatures.length > 0) {
      drawBoundaryLabels(visibleLostHoustonFeatures, 12.8, "#FBCFE8");
    }
    if (state.layers.plattedSubdivisions && this.overlaysData?.platted_subdivisions) {
      drawBoundaryLabels(this.overlaysData.platted_subdivisions.features, 13.0, "#ECFCCB");
    }
    if (state.layers.landUseProtections && this.overlaysData?.land_use_protections) {
      drawBoundaryLabels(this.overlaysData.land_use_protections.features, 15.6, "#FDE68A");
    }
    if (state.layers.neighborhoods && this.overlaysData?.neighborhoods) {
      drawBoundaryLabels(this.overlaysData.neighborhoods.features, 12.2, "#D9F99D");
    }
    if (state.layers.superNeighborhoods && this.overlaysData?.super_neighborhoods) {
      drawBoundaryLabels(this.overlaysData.super_neighborhoods.features, 10.6, "#BFDBFE");
    }
    if (
      state.layers.potentialHistoricDistricts &&
      this.overlaysData?.potential_historic_districts
    ) {
      drawBoundaryLabels(this.overlaysData.potential_historic_districts.features, 12.2, "#FED7AA");
    }
    if (state.layers.culturalDistricts && this.overlaysData?.cultural_districts) {
      drawBoundaryLabels(this.overlaysData.cultural_districts.features, 11.4, "#FBCFE8");
    }

    // 9. Guided Walking Tour Route & Numbered Stop Pins
    if (this.activeTour && Array.isArray(this.activeTour.stops) && this.activeTour.stops.length > 0) {
      const stops = this.activeTour.stops;
      const screenPts = stops.map((s) => this._lngLatToScreen(s.lng, s.lat, width, height));
      const routeCoords =
        Array.isArray(this.activeTour.routeCoords) && this.activeTour.routeCoords.length > 1
          ? this.activeTour.routeCoords
          : stops.map((s) => [s.lng, s.lat]);
      const routeScreenPts = routeCoords.map(([lng, lat]) =>
        this._lngLatToScreen(lng, lat, width, height)
      );

      // Front-walkway connectors from street curb to building pin
      for (let i = 0; i < stops.length; i++) {
        const s = stops[i];
        if (!Number.isFinite(s.street_lng) || !Number.isFinite(s.street_lat)) continue;
        const [cx, cy] = this._lngLatToScreen(s.street_lng, s.street_lat, width, height);
        const [bx, by] = screenPts[i];
        ctx.save();
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(bx, by);
        ctx.lineCap = "round";
        ctx.lineWidth = 3.5;
        ctx.strokeStyle = "rgba(15, 17, 21, 0.78)";
        ctx.stroke();

        ctx.setLineDash([3, 4]);
        ctx.lineWidth = 1.9;
        ctx.strokeStyle = "#95C959";
        ctx.stroke();
        ctx.restore();
      }

      if (routeScreenPts.length > 1) {
        ctx.save();
        ctx.beginPath();
        ctx.moveTo(routeScreenPts[0][0], routeScreenPts[0][1]);
        for (let i = 1; i < routeScreenPts.length; i++) {
          ctx.lineTo(routeScreenPts[i][0], routeScreenPts[i][1]);
        }
        ctx.lineCap = "round";
        ctx.lineJoin = "round";
        ctx.lineWidth = 6.0;
        ctx.strokeStyle = "rgba(15, 17, 21, 0.9)";
        ctx.stroke();

        ctx.setLineDash([7, 5]);
        ctx.lineWidth = 3.1;
        ctx.strokeStyle = "#FDE047";
        ctx.stroke();
        ctx.restore();
      }

      for (let i = 0; i < stops.length; i++) {
        const s = stops[i];
        const [sx, sy] = screenPts[i];
        if (sx < -20 || sx > width + 20 || sy < -20 || sy > height + 20) continue;
        const isActive = i === this.activeTourStopIndex;
        const r = isActive ? 12.5 : 10.5;

        ctx.save();
        ctx.beginPath();
        ctx.arc(sx, sy, r + 4, 0, Math.PI * 2);
        ctx.fillStyle = isActive ? "rgba(253, 224, 71, 0.4)" : "rgba(149, 201, 89, 0.25)";
        ctx.fill();

        ctx.beginPath();
        ctx.arc(sx, sy, r, 0, Math.PI * 2);
        ctx.fillStyle = isActive ? "#FDE047" : "#141820";
        ctx.fill();
        ctx.lineWidth = 2.2;
        ctx.strokeStyle = isActive ? "#0F1115" : "#FDE047";
        ctx.stroke();

        ctx.font = "700 11px Inter, system-ui, sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillStyle = isActive ? "#0F1115" : "#FDE047";
        ctx.fillText(String(i + 1), sx, sy + 0.5);
        ctx.restore();

        cs.renderedBBoxes.push({
          minX: sx - 14,
          minY: sy - 14,
          maxX: sx + 14,
          maxY: sy + 14,
          props: {
            isTourStop: true,
            stopIndex: i,
            landmark_name: `Stop ${i + 1}: ${s.title}`,
            year_built: s.year,
            address: s.story,
          },
        });
      }
    }

    ctx.restore();
    this._renderSpyglassOverlay();
  }

  /* ========================================================================
     Unified Public Controller API
     ======================================================================== */
  _buildTooltipHTML(p, overlapStack = []) {
    const isBoundary =
      p.overlay_layer === "lost_houston" ||
      p.overlay_layer === "neighborhoods" ||
      p.overlay_layer === "platted_subdivisions" ||
      p.overlay_layer === "land_use_protections" ||
      p.overlay_layer === "super_neighborhoods" ||
      p.overlay_layer === "historic_wards" ||
      p.overlay_layer === "historic_districts" ||
      p.overlay_layer === "heritage_districts" ||
      p.overlay_layer === "nrhp_districts" ||
      p.overlay_layer === "potential_historic_districts" ||
      p.overlay_layer === "cultural_districts" ||
      p.overlay_layer === "holc_1937_zones" ||
      p.overlay_layer === "red_book_1915" ||
      p.overlay_layer === "hahc_coa_filings" ||
      p.overlay_layer === "portfolio_work" ||
      p.overlay_layer === "historical_waterways" ||
      p.overlay_layer === "historical_railroads" ||
      p.type === "Neighborhood / Historic Area" ||
      p.type === "Platted Subdivision" ||
      p.type === "COH Super Neighborhood" ||
      p.type === "Historic Ward";

    const extraCount =
      Array.isArray(overlapStack) && overlapStack.length > 1 ? overlapStack.length - 1 : 0;
    const overlapFooterHtml =
      extraCount > 0
        ? `<div class="tooltip-overlap-footer">
             &#128260; <strong>+${extraCount} overlapping ${
            extraCount === 1 ? "feature" : "features"
          }</strong> (${overlapStack
            .slice(1, 4)
            .map((it) => `${it.typeBadge}: ${it.title}`)
            .join(" · ")}) · Click to inspect &amp; cycle
           </div>`
        : "";

    const title =
      p.building_name ||
      p.landmark_name ||
      p.name ||
      p.era_label ||
      p.address ||
      "Historic Property";
    let altList = [];
    if (Array.isArray(p.alt_names)) {
      altList = p.alt_names.map((s) => String(s || "").trim()).filter(Boolean);
    } else if (typeof p.alt_names === "string" && p.alt_names.trim()) {
      const rawAlt = p.alt_names.trim();
      if (rawAlt.startsWith("[")) {
        try {
          const parsed = JSON.parse(rawAlt);
          if (Array.isArray(parsed)) {
            altList = parsed.map((s) => String(s || "").trim()).filter(Boolean);
          }
        } catch (_e) {
          altList = [];
        }
      } else {
        altList = rawAlt
          .split(/\s*\|\s*|\s*;\s*/)
          .map((s) => s.trim())
          .filter(Boolean);
      }
    }
    const akaHtml =
      altList.length > 0
        ? `<div class="tooltip-aka">AKA: ${altList.slice(0, 3).join(", ")}</div>`
        : "";

    if (isBoundary) {
      let badge = p.type_label || p.type || "Neighborhood Boundary";
      if (p.overlay_layer === "lost_houston") {
        badge = `† Lost Houston (${p.year_built}-${p.year_demolished})`;
      } else if (p.overlay_layer === "platted_subdivisions") {
        if (p.has_deed_docs) {
          badge = `📜 Deed Restrictions + Plat (${p.deed_doc_count || 1} PDF${
            Number(p.deed_doc_count) === 1 ? "" : "s"
          })`;
        } else {
          badge = p.plat_citation || (p.vol_page ? `HCAD Plat (Vol ${p.vol_page})` : "Platted Subdivision");
        }
      } else if (p.overlay_layer === "land_use_protections") {
        badge = p.type_label || "Chapter 42 Protection";
      } else if (p.overlay_layer === "historic_wards" && p.era_label) {
        badge = p.era_label;
      } else if (p.overlay_layer === "super_neighborhoods" && p.poly_id) {
        badge = `COH Super Neighborhood #${p.poly_id}`;
      } else if (p.overlay_layer === "historical_waterways") {
        badge = p.type_label || "Historical Waterway";
      } else if (p.overlay_layer === "historical_railroads") {
        badge = p.type_label || "Historical Railroad";
      } else if (p.overlay_layer === "potential_historic_districts") {
        badge = p.already_designated_coh_hd
          ? "2019 Surveyed (Now COH Historic District)"
          : p.survey_subtype === "eligible_site"
          ? "2019 Surveyed NRHP-Eligible Site"
          : "2019 Surveyed Potential Historic District";
      } else if (p.overlay_layer === "cultural_districts") {
        badge = "State-Recognized Cultural District";
      } else if (p.overlay_layer === "holc_1937_zones") {
        badge = `1937 HOLC ${p.zone_code || ""} · ${p.grade_label || `Grade ${p.holc_grade || ""}`}`;
      } else if (p.overlay_layer === "red_book_1915") {
        badge = `1915 Red Book · ${p.category_label || "Heritage Site"}`;
      } else if (p.overlay_layer === "hahc_coa_filings") {
        badge = p.has_active_agenda
          ? `HAHC Active Agenda (${p.latest_meeting_date || "Oct 8, 2026"})`
          : `HAHC COA & Designations (${Number(p.filings_count) || 1} filing${
              Number(p.filings_count) === 1 ? "" : "s"
            })`;
      } else if (p.overlay_layer === "portfolio_work") {
        badge = p.is_lost
          ? `Portfolio Work · Demolished (${p.year_built || "?"}-${p.year_demolished || "?"})`
          : `Portfolio Work · Extant (${p.year_built || "Undated"})`;
      }
      const statParts = [];
      if (p.overlay_layer === "lost_houston") {
        if (p.address) statParts.push(p.address);
        if (p.architect) statParts.push(p.architect);
        if (p.replaced_by) statParts.push(`Site now: ${p.replaced_by}`);
      } else if (p.overlay_layer === "red_book_1915") {
        if (p.historic_address_1915) statParts.push(`1915: ${p.historic_address_1915}`);
        if (p.subtitle) statParts.push(p.subtitle);
        if (p.survival_label) statParts.push(p.survival_label);
      } else if (p.overlay_layer === "hahc_coa_filings") {
        if (p.historic_district) statParts.push(`${p.historic_district} HD`);
        if (p.latest_app_type) statParts.push(p.latest_app_type);
        if (p.latest_outcome_detail) statParts.push(String(p.latest_outcome_detail).slice(0, 72));
      } else if (p.overlay_layer === "portfolio_work") {
        if (p.address) statParts.push(p.address);
        if (p.architect) statParts.push(p.architect);
        if (p.style) statParts.push(p.style);
      } else if (p.overlay_layer === "holc_1937_zones") {
        if (Number(p.building_count) > 0) {
          statParts.push(`${Number(p.building_count).toLocaleString()} structures`);
        }
        if (Number(p.pre_1938_count) > 0) {
          statParts.push(`${Number(p.pre_1938_count).toLocaleString()} pre-1938`);
        }
        if (Number(p.red_book_1915_count) > 0) {
          statParts.push(`${Number(p.red_book_1915_count)} 1915 Red Book sites`);
        }
        if (Number.isFinite(Number(p.af_temp_f)) && Number(p.af_temp_f) > 0) {
          statParts.push(`Heat Watch ${Number(p.af_temp_f).toFixed(1)} °F`);
        }
      } else if (p.overlay_layer === "historical_waterways") {
        if (p.status) statParts.push(p.status);
        if (p.era_notes) statParts.push(p.era_notes);
        if (Number(p.length_miles) > 0) statParts.push(`${p.length_miles} mi mapped`);
      } else if (p.overlay_layer === "historical_railroads") {
        if (p.historic_company) statParts.push(p.historic_company);
        if (p.charter_year) statParts.push(`Opened/Chartered ${p.charter_year}`);
        if (p.status) statParts.push(p.status);
      } else if (p.overlay_layer === "land_use_protections") {
        if (p.ordinance) statParts.push(`Ord. #${p.ordinance}`);
        if (Number(p.min_lot_sqft) > 0) {
          statParts.push(`Min Lot ${Number(p.min_lot_sqft).toLocaleString()} sq ft`);
        }
        if (Number(p.min_bldg_line_ft) > 0) {
          statParts.push(`Min Setback ${p.min_bldg_line_ft} ft`);
        }
      } else {
        if (p.overlay_layer === "platted_subdivisions" && p.plat_citation && p.has_deed_docs) {
          statParts.push(p.plat_citation);
        }
        if (Number(p.building_count) > 0) {
          statParts.push(`${Number(p.building_count).toLocaleString()} structures`);
        }
        if (Number(p.earliest_year) >= 1836) {
          statParts.push(`Earliest ${p.earliest_year}`);
        }
        if (Number(p.median_year) >= 1836) {
          statParts.push(`Median ${p.median_year}`);
        }
        if (Number(p.smls_min_sqft) > 0) {
          statParts.push(`Ch.42 Min Lot ${Number(p.smls_min_sqft).toLocaleString()} sqft`);
        }
      }
      if (!statParts.length && p.neighborhood && p.overlay_layer === "platted_subdivisions") {
        statParts.push(p.neighborhood);
      }
      if (!statParts.length && p.super_neighborhood) {
        statParts.push(p.super_neighborhood);
      }
      const subtitle =
        statParts.join(" • ") || "Click to inspect historical dossier";
      return `<div class="tooltip-card">
        <div class="tooltip-top">
          <span class="tooltip-badge">${badge}</span>
          ${
            p.overlay_layer === "lost_houston" && p.sanborn_material_label
              ? `<span class="tooltip-status">${p.sanborn_material_label}</span>`
              : p.neighborhood && p.overlay_layer === "platted_subdivisions"
              ? `<span class="tooltip-status">${p.neighborhood}</span>`
              : p.historic_ward && p.overlay_layer !== "historic_wards"
              ? `<span class="tooltip-status">${p.historic_ward}</span>`
              : p.watershed && p.overlay_layer === "historical_waterways"
              ? `<span class="tooltip-status">${p.watershed}</span>`
              : ""
          }
        </div>
        <div class="tooltip-title">${title}</div>
        ${akaHtml}
        <div class="tooltip-sub">${subtitle}</div>
        ${overlapFooterHtml}
      </div>`;
    }

    let badge = "";
    if (p.year_built && Number(p.year_built) >= 1836) {
      if (p.is_curated_override) {
        badge = `Built ${p.year_built} ✓ PH Verified`;
      } else if (
        p.year_source === "subdivision_median" ||
        p.year_source === "blockface_median"
      ) {
        badge = `Circa ${p.year_built} (Est.)`;
      } else {
        badge = `Built ${p.year_built}`;
      }
    } else if (p.good_brick_summary) {
      badge = `★ ${p.good_brick_summary}`;
    } else if (p.designation) {
      badge =
        p.protection_tier === "lm_waiver_vulnerable"
          ? "COH Landmark (90-Day Waiver)"
          : p.designation;
    } else if (p.refnum) {
      badge = p.is_demolished_nrhp
        ? `† NRHP #${p.refnum} (Gone)`
        : `NRHP #${p.refnum}`;
    } else if (p.marker_num) {
      badge = `THC Marker #${p.marker_num}`;
    } else if (p.type) {
      badge = p.type;
    } else if (p.era_label) {
      badge =
        p.annex_subtype === "spoke_or_spa"
          ? "Highway Spoke / MUD Limited-Purpose"
          : "Full-Purpose City Boundary";
    } else {
      badge = p.use_category || "Undated Parcel";
    }
    const locSuffix =
      p.historic_district &&
      p.historic_district !== "Outside City District" &&
      p.historic_district !== "Outside Historic District"
        ? ` • ${p.historic_district}`
        : p.neighborhood
        ? ` • ${p.neighborhood}`
        : "";
    const subtitle =
      (p.address && p.address !== title ? `${p.address}${locSuffix}` : "") ||
      p.historic_district ||
      p.neighborhood ||
      p.address ||
      p.subdivision ||
      p.annex_note ||
      (p.era_label && p.decade ? `Annexed through the ${p.decade}s` : "") ||
      (p.type ? "Preservation District Boundary" : "Click to inspect property record");

    const goodBrickPill =
      p.good_brick_summary && badge !== `★ ${p.good_brick_summary}`
        ? `<span class="tooltip-good-brick">&#9733; ${p.good_brick_summary}</span>`
        : "";

    const matchedPermitAlert = this.getPermitAlertForBuilding({
      hcadNum: p.hcad_num,
      buildingId: p.building_id || p.id,
    });
    const permitAlertPill = matchedPermitAlert
      ? `<span class="tooltip-permit-alert ${
          matchedPermitAlert.severity === "critical" ? "critical" : "watch"
        }">&#9888; ${
          matchedPermitAlert.severity === "critical"
            ? "DEMO / SEWER ALERT"
            : "PERMIT / ABATEMENT WATCH"
        }</span>`
      : "";
    const permitAlertNoteHtml = matchedPermitAlert?.headline
      ? `<div class="tooltip-permit-alert-note ${
          matchedPermitAlert.severity === "critical" ? "critical" : "watch"
        }">${matchedPermitAlert.headline}</div>`
      : "";

    return `<div class="tooltip-card">
      <div class="tooltip-top">
        <span class="tooltip-badge">${badge}</span>
        ${permitAlertPill}
        ${goodBrickPill}
        ${
          p.contributing && p.contributing !== "Outside Historic District"
            ? `<span class="tooltip-status">${p.contributing}</span>`
            : ""
        }
      </div>
      <div class="tooltip-title">${title}</div>
      ${akaHtml}
      <div class="tooltip-sub">${subtitle}</div>
      ${permitAlertNoteHtml}
      ${overlapFooterHtml}
    </div>`;
  }

  syncWithState(state) {
    if (!this.isReady) return;
    if (this.useCanvasFallback) {
      this._renderCanvas2D();
      return;
    }
    if (!this.map) return;

    this._syncWebGLHistoricMapSource(state);
    this._renderSpyglassOverlay();

    const isNoMap = state.basemap === "solid_dark" || state.basemap === "solid_light";
    const isLightBg = state.basemap === "solid_light" || state.basemap === "warm_parchment";
    const bgHex =
      state.basemap === "solid_light"
        ? "#F4F1EA"
        : state.basemap === "warm_parchment"
        ? "#EBE6DC"
        : "#0D1117";
    if (this.container) {
      this.container.style.backgroundColor = bgHex;
    }
    if (this.map.getLayer("basemap-solid-bg")) {
      this.map.setPaintProperty("basemap-solid-bg", "background-color", bgHex);
    }
    if (this.map.getLayer("vector-roads-highzoom")) {
      this.map.setLayoutProperty(
        "vector-roads-highzoom",
        "visibility",
        isNoMap ? "none" : "visible"
      );
    }
    if (this.map.getLayer("platted-subdivisions-line")) {
      this.map.setPaintProperty("platted-subdivisions-line", "line-color", [
        "case",
        ["boolean", ["get", "has_deed_docs"], false],
        isLightBg ? "#D97706" : "#FBBF24",
        isLightBg ? "#0891B2" : "#22D3EE",
      ]);
    }

    this.map.setLayoutProperty(
      "basemap-dark-layer",
      "visibility",
      state.basemap === "dark_archival" ? "visible" : "none"
    );
    this.map.setLayoutProperty(
      "basemap-dark-labels-layer",
      "visibility",
      state.basemap === "dark_archival" ? "visible" : "none"
    );
    this.map.setLayoutProperty(
      "basemap-light-layer",
      "visibility",
      state.basemap === "warm_parchment" ? "visible" : "none"
    );
    this.map.setLayoutProperty(
      "basemap-light-labels-layer",
      "visibility",
      state.basemap === "warm_parchment" ? "visible" : "none"
    );
    this.map.setLayoutProperty(
      "basemap-satellite-layer",
      "visibility",
      state.basemap === "satellite" ? "visible" : "none"
    );
    this.map.setLayoutProperty(
      "basemap-satellite-labels-layer",
      "visibility",
      state.basemap === "satellite" ? "visible" : "none"
    );

    if (this.map.getLayer("basemap-historic-topo-layer")) {
      this.map.setLayoutProperty(
        "basemap-historic-topo-layer",
        "visibility",
        state.layers?.historicMap && !state.historicMapSpyglass ? "visible" : "none"
      );
      const histOpacity = Math.max(
        0.1,
        Math.min(1, (Number(state.historicMapOpacity) || 82) / 100)
      );
      this.map.setPaintProperty("basemap-historic-topo-layer", "raster-opacity", histOpacity);
    }

    const colorExpr = buildColorExpression(state.colorMode, state.paletteStyle);
    const baseFilterExpr = buildFeatureFilterExpression(state);
    const filterExpr = this._buildTourFocusFilterExpression(baseFilterExpr);
    const shardFilterExpr = this._buildShardLayerFilter(filterExpr);
    const lostHoustonFilterExpr = buildLostHoustonFilterExpression(state);

    for (const layerId of [
      ...this.buildingFillLayerIds,
      ...this.buildingLineLayerIds,
      ...this.buildingExtrusionLayerIds,
    ]) {
      if (this.map.getLayer(layerId)) {
        this.map.setFilter(layerId, shardFilterExpr);
      }
    }
    for (const hsLayerId of ["historic-status-buildings-fill", "historic-status-buildings-extrusion"]) {
      if (this.map.getLayer(hsLayerId)) {
        this.map.setFilter(hsLayerId, ["all", ["has", "protection_tier"], shardFilterExpr]);
      }
    }

    for (const layerId of [
      "curated-overrides-fill",
      "curated-overrides-line",
      "curated-overrides-extrusion",
      "parcels-fill",
      "parcels-line",
    ]) {
      if (this.map.getLayer(layerId)) {
        this.map.setFilter(layerId, filterExpr);
      }
    }

    for (const fillId of [
      ...this.buildingFillLayerIds,
      "historic-status-buildings-fill",
      "curated-overrides-fill",
    ]) {
      if (this.map.getLayer(fillId)) {
        this.map.setPaintProperty(fillId, "fill-color", colorExpr);
      }
    }
    for (const extId of [
      ...this.buildingExtrusionLayerIds,
      "historic-status-buildings-extrusion",
      "curated-overrides-extrusion",
    ]) {
      if (this.map.getLayer(extId)) {
        this.map.setPaintProperty(extId, "fill-extrusion-color", colorExpr);
      }
    }
    if (this.map.getLayer("parcels-fill")) {
      this.map.setPaintProperty("parcels-fill", "fill-color", colorExpr);
    }

    const iso = this.isolatedBoundary;
    const isoGeom = iso?.feature?.geometry || null;
    const isoMode = iso?.mode || "contents";
    const allowIsoStructures = !isoGeom || isoMode !== "border_only";
    const allowIsoMarkers = !isoGeom || isoMode === "contents";

    const showBuildings =
      allowIsoStructures && (state.renderMode === "buildings" || state.renderMode === "both");
    const showParcelsFill = allowIsoStructures && state.renderMode === "parcels";
    const showParcelsLine =
      allowIsoStructures && (state.renderMode === "both" || state.renderMode === "parcels");

    const setVis = (ids, visible) => {
      for (const id of ids) {
        if (this.map.getLayer(id)) {
          this.map.setLayoutProperty(id, "visibility", visible ? "visible" : "none");
        }
      }
    };

    setVis(
      [...this.buildingFillLayerIds, "curated-overrides-fill"],
      showBuildings && !state.extrude3D
    );
    setVis(
      ["historic-status-buildings-fill"],
      showBuildings && !state.extrude3D && state.colorMode === "preservation_status"
    );
    setVis(
      [...this.buildingLineLayerIds, "curated-overrides-line"],
      showBuildings && !state.extrude3D
    );
    setVis(
      [...this.buildingExtrusionLayerIds, "curated-overrides-extrusion"],
      showBuildings && state.extrude3D
    );
    setVis(
      ["historic-status-buildings-extrusion"],
      showBuildings && state.extrude3D && state.colorMode === "preservation_status"
    );
    setVis(
      ["protection-armor-zones-fill", "protection-armor-zones-line"],
      !isoGeom && state.colorMode === "preservation_status"
    );
    const selectedPassesFilter =
      !this.selectedFeatureProps || featureMatchesFilter(this.selectedFeatureProps, state);
    setVis(
      ["selected-feature-outline", ...(this.highlightLayerIds || [])],
      showBuildings && !state.extrude3D && selectedPassesFilter
    );
    setVis(
      ["selected-feature-3d-extrusion"],
      showBuildings && state.extrude3D && selectedPassesFilter
    );
    if (state.extrude3D && this.selectedFeatureProps) {
      this._updateSelectedFeature3DSource(state);
    }
    setVis(["parcels-fill"], showParcelsFill);
    setVis(["parcels-line"], showParcelsLine);

    const showLostHouston =
      !isoGeom &&
      (Boolean(state.layers?.lostHouston) || Boolean(state.historicalSnapshotMode));
    setVis(["lost-houston-fill", "lost-houston-line", "lost-houston-label"], showLostHouston);
    setVis(["lost-houston-extrusion"], showLostHouston && Boolean(state.extrude3D));
    if (showLostHouston) {
      for (const lhLayer of [
        "lost-houston-fill",
        "lost-houston-line",
        "lost-houston-extrusion",
        "lost-houston-label",
      ]) {
        if (this.map.getLayer(lhLayer)) {
          this.map.setFilter(lhLayer, lostHoustonFilterExpr);
        }
      }
    }

    setVis(
      ["good-brick-glow", "good-brick-circle"],
      allowIsoMarkers && Boolean(state.layers.goodBrickAwards)
    );
    setVis(
      ["relocated-structures-glow", "relocated-structures-circle"],
      allowIsoMarkers && Boolean(state.layers.relocatedStructures)
    );
    setVis(
      ["permit-watch-outer-glow", "permit-watch-ring", "permit-watch-circle"],
      allowIsoMarkers && Boolean(state.layers.permitWatch)
    );
    setVis(
      ["plat-watch-outer-glow", "plat-watch-ring", "plat-watch-circle"],
      allowIsoMarkers && Boolean(state.layers.platWatch)
    );
    setVis(
      ["landmarks-waiver-ring", "landmarks-circle"],
      allowIsoMarkers && Boolean(state.layers.landmarks)
    );
    setVis(
      ["nrhp-properties-glow", "nrhp-properties-circle"],
      allowIsoMarkers && Boolean(state.layers.nrhpProperties)
    );
    setVis(["thc-markers-circle"], allowIsoMarkers && Boolean(state.layers.thcMarkers));
    setVis(
      ["red-book-1915-glow", "red-book-1915-circle"],
      allowIsoMarkers && Boolean(state.layers?.redBook1915)
    );
    setVis(
      ["hahc-coa-glow", "hahc-coa-circle"],
      allowIsoMarkers && Boolean(state.layers?.hahcTracker)
    );

    if (isoGeom && allowIsoMarkers) {
      const isoBBox = iso.bbox || null;
      const inIso = (coords) => {
        if (!Array.isArray(coords) || coords.length < 2) return false;
        const [lng, lat] = coords;
        if (isoBBox && (lng < isoBBox[0] || lng > isoBBox[2] || lat < isoBBox[1] || lat > isoBBox[3])) {
          return false;
        }
        return this._pointInPolygonGeometry(lng, lat, isoGeom);
      };
      const gbIds = (this.overlaysData?.good_brick_awards?.features || [])
        .filter((f) => inIso(f.geometry?.coordinates))
        .map((f) => String(f.properties?.id || f.properties?.hcad_num || ""));
      const lmIds = (this.overlaysData?.landmarks?.features || [])
        .filter((f) => inIso(f.geometry?.coordinates))
        .map((f) => String(f.properties?.id || f.properties?.hcad_num || ""));
      const nrhpIds = (this.overlaysData?.nrhp_properties?.features || [])
        .filter((f) => inIso(f.geometry?.coordinates))
        .map((f) => String(f.properties?.id || f.properties?.refnum || ""));
      const thcNums = (this.overlaysData?.thc_markers?.features || [])
        .filter((f) => inIso(f.geometry?.coordinates))
        .map((f) => String(f.properties?.marker_num || f.properties?.id || ""));
      const rbIds = (this.overlaysData?.red_book_1915?.features || [])
        .filter((f) => inIso(f.geometry?.coordinates))
        .map((f) => String(f.properties?.id || ""));
      const hahcIds = (this.overlaysData?.hahc_coa_filings?.features || [])
        .filter((f) => inIso(f.geometry?.coordinates))
        .map((f) => String(f.properties?.id || ""));
      const pwIds = (this.permitAlertsFC?.features || [])
        .filter((f) => inIso(f.geometry?.coordinates))
        .map((f) => String(f.properties?.id || f.properties?.hcad_num || ""));

      const gbFilter = ["in", ["coalesce", ["get", "id"], ["get", "hcad_num"], ""], ["literal", gbIds]];
      const lmFilter = buildLandmarksFilterExpression(state, lmIds);
      const rbFilter = buildRedBookFilterExpression(state, rbIds);
      const hahcFilter = buildHahcFilterExpression(state, hahcIds);
      const nrhpIsoFilter = ["in", ["coalesce", ["get", "id"], ["get", "refnum"], ""], ["literal", nrhpIds]];
      const thcFilter = [
        "in",
        ["to-string", ["coalesce", ["get", "marker_num"], ["get", "id"], ""]],
        ["literal", thcNums],
      ];
      const pwFilter = ["in", ["coalesce", ["get", "id"], ["get", "hcad_num"], ""], ["literal", pwIds]];
      if (this.map.getLayer("good-brick-glow")) this.map.setFilter("good-brick-glow", gbFilter);
      if (this.map.getLayer("good-brick-circle")) this.map.setFilter("good-brick-circle", gbFilter);
      if (this.map.getLayer("landmarks-waiver-ring")) this.map.setFilter("landmarks-waiver-ring", lmFilter);
      if (this.map.getLayer("landmarks-circle")) this.map.setFilter("landmarks-circle", lmFilter);
      if (this.map.getLayer("red-book-1915-glow")) {
        this.map.setFilter(
          "red-book-1915-glow",
          rbFilter
            ? ["all", ["==", ["get", "survival_status"], "surviving_era_structure"], rbFilter]
            : ["==", ["get", "survival_status"], "surviving_era_structure"]
        );
      }
      if (this.map.getLayer("red-book-1915-circle")) {
        this.map.setFilter("red-book-1915-circle", rbFilter);
      }
      if (this.map.getLayer("hahc-coa-glow")) {
        this.map.setFilter("hahc-coa-glow", hahcFilter);
      }
      if (this.map.getLayer("hahc-coa-circle")) {
        this.map.setFilter("hahc-coa-circle", hahcFilter);
      }
      if (this.map.getLayer("nrhp-properties-glow")) {
        this.map.setFilter("nrhp-properties-glow", [
          "all",
          ["!", ["boolean", ["get", "is_demolished_nrhp"], false]],
          nrhpIsoFilter,
        ]);
      }
      if (this.map.getLayer("nrhp-properties-circle")) this.map.setFilter("nrhp-properties-circle", nrhpIsoFilter);
      if (this.map.getLayer("thc-markers-circle")) this.map.setFilter("thc-markers-circle", thcFilter);
      if (this.map.getLayer("permit-watch-outer-glow")) this.map.setFilter("permit-watch-outer-glow", pwFilter);
      if (this.map.getLayer("permit-watch-ring")) this.map.setFilter("permit-watch-ring", pwFilter);
      if (this.map.getLayer("permit-watch-circle")) this.map.setFilter("permit-watch-circle", pwFilter);
    } else if (!isoGeom) {
      const lmFilter = buildLandmarksFilterExpression(state, null);
      const rbFilter = buildRedBookFilterExpression(state, null);
      const hahcFilter = buildHahcFilterExpression(state, null);
      if (this.map.getLayer("good-brick-glow")) this.map.setFilter("good-brick-glow", null);
      if (this.map.getLayer("good-brick-circle")) this.map.setFilter("good-brick-circle", null);
      if (this.map.getLayer("landmarks-waiver-ring")) this.map.setFilter("landmarks-waiver-ring", lmFilter);
      if (this.map.getLayer("landmarks-circle")) this.map.setFilter("landmarks-circle", lmFilter);
      if (this.map.getLayer("red-book-1915-glow")) {
        this.map.setFilter(
          "red-book-1915-glow",
          rbFilter
            ? ["all", ["==", ["get", "survival_status"], "surviving_era_structure"], rbFilter]
            : ["==", ["get", "survival_status"], "surviving_era_structure"]
        );
      }
      if (this.map.getLayer("red-book-1915-circle")) {
        this.map.setFilter("red-book-1915-circle", rbFilter);
      }
      if (this.map.getLayer("hahc-coa-glow")) {
        this.map.setFilter("hahc-coa-glow", hahcFilter);
      }
      if (this.map.getLayer("hahc-coa-circle")) {
        this.map.setFilter("hahc-coa-circle", hahcFilter);
      }
      if (this.map.getLayer("nrhp-properties-glow")) {
        this.map.setFilter("nrhp-properties-glow", [
          "!",
          ["boolean", ["get", "is_demolished_nrhp"], false],
        ]);
      }
      if (this.map.getLayer("nrhp-properties-circle")) this.map.setFilter("nrhp-properties-circle", null);
      if (this.map.getLayer("thc-markers-circle")) this.map.setFilter("thc-markers-circle", null);
      if (this.map.getLayer("permit-watch-outer-glow")) this.map.setFilter("permit-watch-outer-glow", null);
      if (this.map.getLayer("permit-watch-ring")) this.map.setFilter("permit-watch-ring", null);
      if (this.map.getLayer("permit-watch-circle")) this.map.setFilter("permit-watch-circle", null);
    }

    setVis(
      ["historic-districts-fill", "historic-districts-line"],
      !isoGeom && Boolean(state.layers.historicDistricts)
    );
    setVis(
      ["heritage-districts-fill", "heritage-districts-line"],
      !isoGeom && Boolean(state.layers.heritageDistricts)
    );
    setVis(
      ["nrhp-districts-fill", "nrhp-districts-line"],
      !isoGeom && Boolean(state.layers.nrhpDistricts)
    );
    setVis(
      [
        "potential-historic-districts-fill",
        "potential-historic-districts-line",
        "potential-historic-districts-label",
      ],
      !isoGeom && Boolean(state.layers?.potentialHistoricDistricts)
    );
    setVis(
      [
        "cultural-districts-fill",
        "cultural-districts-line",
        "cultural-districts-label",
      ],
      !isoGeom && Boolean(state.layers?.culturalDistricts)
    );
    const showHolc = !isoGeom && Boolean(state.layers?.holc1937);
    setVis(
      ["holc-1937-zones-fill", "holc-1937-zones-line", "holc-1937-zones-label"],
      showHolc
    );
    if (showHolc) {
      const holcFilter = buildHolcZonesFilterExpression(state);
      for (const hLayer of [
        "holc-1937-zones-fill",
        "holc-1937-zones-line",
        "holc-1937-zones-label",
      ]) {
        if (this.map.getLayer(hLayer)) {
          this.map.setFilter(hLayer, holcFilter);
        }
      }
    }

    setVis(
      ["neighborhoods-fill", "neighborhoods-line", "neighborhoods-label"],
      !isoGeom && Boolean(state.layers?.neighborhoods)
    );
    setVis(
      [
        "platted-subdivisions-fill",
        "platted-subdivisions-line",
        "platted-subdivisions-label",
      ],
      !isoGeom && Boolean(state.layers?.plattedSubdivisions)
    );
    setVis(
      [
        "land-use-protections-fill",
        "land-use-protections-line",
        "land-use-protections-label",
      ],
      !isoGeom && Boolean(state.layers?.landUseProtections)
    );
    setVis(
      ["super-neighborhoods-fill", "super-neighborhoods-line", "super-neighborhoods-label"],
      !isoGeom && Boolean(state.layers?.superNeighborhoods)
    );

    const showWards = !isoGeom && Boolean(state.layers?.historicWards);
    setVis(
      ["historic-wards-fill", "historic-wards-line", "historic-wards-label"],
      showWards
    );
    if (showWards) {
      const wardFilter = buildHistoricWardFilterExpression(state);
      for (const wLayer of [
        "historic-wards-fill",
        "historic-wards-line",
        "historic-wards-label",
      ]) {
        if (this.map.getLayer(wLayer)) {
          this.map.setFilter(wLayer, wardFilter);
        }
      }
    }

    const showAnnex = !isoGeom && Boolean(state.layers?.annexations);
    setVis(["annexations-fill", "annexations-line"], showAnnex);
    if (showAnnex) {
      const annexFilter = buildAnnexationFilterExpression(state);
      this.map.setFilter("annexations-fill", annexFilter);
      this.map.setFilter("annexations-line", annexFilter);
    }

    setVis(
      [
        "historical-waterways-casing",
        "historical-waterways-line-solid",
        "historical-waterways-line-dashed",
        "historical-waterways-label",
      ],
      !isoGeom && Boolean(state.layers?.historicalWaterways)
    );
    setVis(
      [
        "historical-railroads-casing",
        "historical-railroads-line-solid",
        "historical-railroads-line-dashed",
        "historical-railroads-ties",
        "historical-railroads-depots",
        "historical-railroads-label",
        "historical-railroads-depots-label",
      ],
      !isoGeom && Boolean(state.layers?.historicalRailroads)
    );

    setVis(
      ["selected-boundary-fill", "selected-boundary-line", "selected-boundary-point"],
      !isoGeom
    );

    // Update WebGL isolation mask and border layers
    const maskSrc = this.map.getSource("isolation-mask-src");
    const borderSrc = this.map.getSource("isolation-border-src");
    if (isoGeom && maskSrc && borderSrc) {
      const worldRing = [
        [-180, -85],
        [180, -85],
        [180, 85],
        [-180, 85],
        [-180, -85],
      ];
      const polys = isoGeom.type === "Polygon" ? [isoGeom.coordinates] : isoGeom.coordinates || [];
      const outerHoles = [];
      const islandFeatures = [];
      for (const poly of polys) {
        if (!Array.isArray(poly) || !poly[0]) continue;
        outerHoles.push(poly[0]);
        for (let h = 1; h < poly.length; h++) {
          if (Array.isArray(poly[h]) && poly[h].length >= 3) {
            islandFeatures.push({
              type: "Feature",
              geometry: { type: "Polygon", coordinates: [poly[h]] },
              properties: {},
            });
          }
        }
      }
      maskSrc.setData({
        type: "FeatureCollection",
        features: [
          {
            type: "Feature",
            geometry: {
              type: "Polygon",
              coordinates: [worldRing, ...outerHoles],
            },
            properties: {},
          },
          ...islandFeatures,
        ],
      });
      borderSrc.setData({
        type: "FeatureCollection",
        features: [iso.feature],
      });
      if (this.map.getLayer("isolation-mask-fill")) {
        this.map.setPaintProperty("isolation-mask-fill", "fill-color", bgHex);
        this.map.setPaintProperty(
          "isolation-mask-fill",
          "fill-opacity",
          isNoMap ? 1.0 : 0.9
        );
      }
      if (this.map.getLayer("isolation-border-fill")) {
        this.map.setPaintProperty(
          "isolation-border-fill",
          "fill-color",
          isLightBg ? "#0F172A" : "#95C959"
        );
        this.map.setPaintProperty(
          "isolation-border-fill",
          "fill-opacity",
          isoMode === "border_only" ? 0.14 : 0.03
        );
      }
      if (this.map.getLayer("isolation-border-line")) {
        this.map.setPaintProperty(
          "isolation-border-line",
          "line-color",
          isoMode === "border_only"
            ? isLightBg
              ? "#0F172A"
              : "#95C959"
            : isLightBg
            ? "#0F172A"
            : "#FDE047"
        );
        this.map.setPaintProperty(
          "isolation-border-line",
          "line-width",
          isoMode === "border_only" ? 3.5 : 2.8
        );
      }
      setVis(["isolation-mask-fill"], true);
      setVis(["isolation-border-fill"], isoMode === "border_only");
      setVis(["isolation-border-line"], isoMode !== "footprints_only");
    } else {
      if (maskSrc) maskSrc.setData({ type: "FeatureCollection", features: [] });
      if (borderSrc) borderSrc.setData({ type: "FeatureCollection", features: [] });
      setVis(["isolation-mask-fill", "isolation-border-fill", "isolation-border-line"], false);
    }

    if (!isoGeom && this.selectedBoundaryFeature?.properties?.overlay_layer) {
      const selOverlay = this.selectedBoundaryFeature.properties.overlay_layer;
      const overlayEnabledMap = {
        lost_houston: showLostHouston,
        historic_districts: Boolean(state.layers?.historicDistricts),
        heritage_districts: Boolean(state.layers?.heritageDistricts),
        nrhp_districts: Boolean(state.layers?.nrhpDistricts),
        potential_historic_districts: Boolean(state.layers?.potentialHistoricDistricts),
        cultural_districts: Boolean(state.layers?.culturalDistricts),
        holc_1937_zones: showHolc,
        red_book_1915: Boolean(state.layers?.redBook1915),
        hahc_coa_filings: Boolean(state.layers?.hahcTracker),
        neighborhoods: Boolean(state.layers?.neighborhoods),
        platted_subdivisions: Boolean(state.layers?.plattedSubdivisions),
        land_use_protections: Boolean(state.layers?.landUseProtections),
        super_neighborhoods: Boolean(state.layers?.superNeighborhoods),
        historic_wards: showWards,
        annexations: showAnnex,
        historical_waterways: Boolean(state.layers?.historicalWaterways),
        historical_railroads: Boolean(state.layers?.historicalRailroads),
      };
      const isSpokeHidden =
        selOverlay === "annexations" &&
        state.showAnnexationSpokes === false &&
        this.selectedBoundaryFeature.properties.annex_subtype === "spoke_or_spa";
      if (overlayEnabledMap[selOverlay] === false || isSpokeHidden) {
        this.clearHighlightedBoundary();
      }
    }

    if (this.selectedFeatureProps) {
      this._updateSelectedFeature3DSource(state);
    }
  }

  toggle3DPitch(enable3D) {
    this.setCameraPitch(enable3D ? 50 : 0, enable3D ? -12 : 0);
  }

  setCameraPitch(pitch, bearing = null) {
    const targetPitch = Math.max(0, Math.min(65, Number(pitch) || 0));
    if (this.useCanvasFallback && this.canvasState) {
      this.canvasState.pitch = targetPitch;
      this._renderCanvas2D();
      if (this.onPitchChange) {
        this.onPitchChange(targetPitch);
      }
      return;
    }
    if (!this.map) return;
    const opts = {
      pitch: targetPitch,
      duration: 600,
    };
    if (bearing !== null) {
      opts.bearing = bearing;
    } else if (targetPitch === 0) {
      opts.bearing = 0;
    } else if (Math.abs(this.map.getBearing()) < 2) {
      opts.bearing = -12;
    }
    this.map.easeTo(opts);
    if (this.onPitchChange) {
      this.onPitchChange(targetPitch);
    }
  }

  getCameraPitch() {
    if (this.useCanvasFallback && this.canvasState) {
      return Math.round(this.canvasState.pitch || 0);
    }
    if (this.map) {
      return Math.round(this.map.getPitch() || 0);
    }
    return 0;
  }

  getCurrentViewport() {
    if (this.useCanvasFallback && this.canvasState) {
      return {
        lat: Number(this.canvasState.lat) || 29.7662,
        lng: Number(this.canvasState.lng) || -95.3805,
        zoom: Number(this.canvasState.zoom) || 15.2,
        pitch: Math.round(Number(this.canvasState.pitch) || 0),
      };
    }
    if (this.map) {
      const c = this.map.getCenter();
      return {
        lat: Number(c.lat) || 29.7662,
        lng: Number(c.lng) || -95.3805,
        zoom: Number(this.map.getZoom()) || 15.2,
        pitch: Math.round(Number(this.map.getPitch()) || 0),
      };
    }
    return {
      lat: 29.7662,
      lng: -95.3805,
      zoom: 15.2,
      pitch: 0,
    };
  }

  selectFeatureByIdOrHcad({
    hcadNum = "",
    featureId = "",
    addressQuery = "",
    fallbackYearBuilt = 0,
    targetLng = null,
    targetLat = null,
    extraCandidateHcads = [],
    flyTo = false,
  }) {
    const hasCoords = Number.isFinite(Number(targetLng)) && Number.isFinite(Number(targetLat));
    if (!featureId && !hcadNum && !addressQuery && !hasCoords) return false;
    const navSeq = (this._selectNavSeq = (this._selectNavSeq || 0) + 1);

    const normAddrStr = (s) =>
      String(s || "")
        .toUpperCase()
        .replace(/\./g, "")
        .replace(/\bSTREET\b/g, "ST")
        .replace(/\bAVENUE\b/g, "AVE")
        .replace(/\bDRIVE\b/g, "DR")
        .replace(/\bROAD\b/g, "RD")
        .replace(/\bBOULEVARD\b/g, "BLVD")
        .replace(/\bLANE\b/g, "LN")
        .replace(/\bCOURT\b/g, "CT")
        .replace(/\bPLACE\b/g, "PL")
        .replace(/\bNORTH\b/g, "N")
        .replace(/\bSOUTH\b/g, "S")
        .replace(/\bEAST\b/g, "E")
        .replace(/\bWEST\b/g, "W")
        .replace(/\s+/g, " ")
        .trim();
    const normTargetAddr = /^\d+\s+/.test(String(addressQuery || "").trim())
      ? normAddrStr(addressQuery)
      : "";

    const alertsList = Array.isArray(this.permitAlertsPayload?.alerts)
      ? this.permitAlertsPayload.alerts
      : [];
    const matchedAlert =
      alertsList.find((al) => {
        if (
          hcadNum &&
          (al.hcad_num === hcadNum ||
            (Array.isArray(al.related_hcads) && al.related_hcads.includes(hcadNum)))
        ) {
          return true;
        }
        if (
          featureId &&
          (al.building_id === featureId ||
            (Array.isArray(al.related_building_ids) && al.related_building_ids.includes(featureId)))
        ) {
          return true;
        }
        if (normTargetAddr && al.address && normAddrStr(al.address) === normTargetAddr) {
          return true;
        }
        return false;
      }) || null;

    let matchedPlat =
      this.getPlatWatchForBuilding({ hcadNum, buildingId: featureId }) || null;
    if (!matchedPlat && Array.isArray(extraCandidateHcads)) {
      for (const eh of extraCandidateHcads) {
        matchedPlat = this.getPlatWatchForBuilding({ hcadNum: eh });
        if (matchedPlat) break;
      }
    }
    if (!matchedPlat && normTargetAddr && this.platWatchByHcad) {
      for (const [, pItem] of this.platWatchByHcad.entries()) {
        if (pItem?.address && normAddrStr(pItem.address) === normTargetAddr) {
          matchedPlat = pItem;
          break;
        }
      }
    }

    const candidateHcads = new Set(
      [
        hcadNum,
        hcadNum && hcadNum.includes("#") ? hcadNum.split("#")[0] : "",
        ...(Array.isArray(extraCandidateHcads) ? extraCandidateHcads : []),
        matchedAlert?.hcad_num || "",
        ...(Array.isArray(matchedAlert?.related_hcads) ? matchedAlert.related_hcads : []),
        matchedPlat?.hcad_num || "",
        ...(Array.isArray(matchedPlat?.related_hcads) ? matchedPlat.related_hcads : []),
      ]
        .map((s) => String(s || "").trim())
        .filter(Boolean)
    );
    const candidateBldIds = new Set(
      [
        featureId,
        matchedAlert?.building_id || "",
        ...(Array.isArray(matchedAlert?.related_building_ids) ? matchedAlert.related_building_ids : []),
        matchedPlat?.building_id || "",
      ]
        .map((s) => String(s || "").trim())
        .filter(Boolean)
    );

    const gbFeatures = this.overlaysData?.good_brick_awards?.features || [];
    const gbMatch =
      gbFeatures.find((f) => {
        const p = f.properties || {};
        return (
          (p.id && candidateBldIds.has(String(p.id))) ||
          (p.building_id && candidateBldIds.has(String(p.building_id))) ||
          (p.hcad_num && candidateHcads.has(String(p.hcad_num)))
        );
      }) || null;

    if (gbMatch?.properties?.building_id) {
      candidateBldIds.add(String(gbMatch.properties.building_id));
    }
    const ovFeatures = this.overridesFC?.features || [];

    const matchesCandidate = (f) => {
      const p = f?.properties || {};
      const pid = String(p.id || "").trim();
      const pbid = String(p.building_id || "").trim();
      const phcad = String(p.hcad_num || "").trim();
      if (
        (pid && (candidateBldIds.has(pid) || candidateHcads.has(pid))) ||
        (pbid && (candidateBldIds.has(pbid) || candidateHcads.has(pbid))) ||
        (phcad && candidateHcads.has(phcad))
      ) {
        return true;
      }
      if (normTargetAddr && p.address && normAddrStr(p.address) === normTargetAddr) {
        return true;
      }
      return false;
    };

    let renderedTileMatch = null;
    if (!this.useCanvasFallback && this.map) {
      const tileLayers = [
        ...(this.buildingFillLayerIds || []),
        ...(this.buildingExtrusionLayerIds || []),
      ].filter((id) => this.map.getLayer(id));
      if (tileLayers.length > 0) {
        const rendered = this.map.queryRenderedFeatures({ layers: tileLayers });
        renderedTileMatch = rendered.find(matchesCandidate) || null;
      }
      if (!renderedTileMatch && candidateHcads.size > 0 && Array.isArray(this.shardSourceIds)) {
        const hcadArr = Array.from(candidateHcads);
        for (const srcId of this.shardSourceIds) {
          try {
            const hits = this.map.querySourceFeatures(srcId, {
              sourceLayer: "buildings",
              filter: ["in", ["get", "hcad_num"], ["literal", hcadArr]],
            });
            const foundHit = hits.find(matchesCandidate) || hits[0] || null;
            if (foundHit) {
              renderedTileMatch = foundHit;
              break;
            }
          } catch (_e) {}
        }
      }
    }

    const match =
      ovFeatures.find(matchesCandidate) ||
      renderedTileMatch ||
      this.buildingsData.find(matchesCandidate) ||
      gbMatch ||
      (() => {
        if (!featureId && !hcadNum && !normTargetAddr) return null;
        const overlayCollections = [
          ["relocated_structures", this.overlaysData?.relocated_structures?.features],
          ["thc_markers", this.overlaysData?.thc_markers?.features],
          ["historical_railroads", this.overlaysData?.historical_railroads?.features],
          ["historical_waterways", this.overlaysData?.historical_waterways?.features],
        ];
        for (const [ovKey, list] of overlayCollections) {
          if (!Array.isArray(list)) continue;
          const found = list.find((f) => {
            const p = f.properties || {};
            return (
              matchesCandidate(f) ||
              (featureId && p.marker_num === featureId)
            );
          });
          if (found) {
            if (ovKey === "historical_railroads" || ovKey === "historical_waterways") {
              return {
                ...found,
                properties: {
                  ...(found.properties || {}),
                  overlay_layer: ovKey,
                  is_boundary_feature: true,
                },
              };
            }
            return found;
          }
        }
        return null;
      })();

    const matchedLm = hcadNum ? this.landmarksByHcad?.get?.(hcadNum)?.[0] || null : null;
    const matchedHahc = hcadNum ? this.hahcFilingsByHcad?.get?.(hcadNum)?.[0] || null : null;
    const matchedRb = hcadNum ? this.redBookByHcad?.get?.(hcadNum)?.[0] || null : null;
    const lmCoord =
      Array.isArray(matchedLm?._coords) && matchedLm._coords.length >= 2
        ? matchedLm._coords
        : null;
    const rbCoord =
      Array.isArray(matchedRb?._coords) && matchedRb._coords.length >= 2
        ? matchedRb._coords
        : null;

    const effLng = Number.isFinite(Number(targetLng))
      ? Number(targetLng)
      : Number.isFinite(Number(matchedAlert?.lng))
      ? Number(matchedAlert.lng)
      : Number.isFinite(Number(matchedPlat?.lng))
      ? Number(matchedPlat.lng)
      : Number.isFinite(Number(matchedHahc?.lng))
      ? Number(matchedHahc.lng)
      : lmCoord && Number.isFinite(Number(lmCoord[0]))
      ? Number(lmCoord[0])
      : rbCoord && Number.isFinite(Number(rbCoord[0]))
      ? Number(rbCoord[0])
      : null;
    const effLat = Number.isFinite(Number(targetLat))
      ? Number(targetLat)
      : Number.isFinite(Number(matchedAlert?.lat))
      ? Number(matchedAlert.lat)
      : Number.isFinite(Number(matchedPlat?.lat))
      ? Number(matchedPlat.lat)
      : Number.isFinite(Number(matchedHahc?.lat))
      ? Number(matchedHahc.lat)
      : lmCoord && Number.isFinite(Number(lmCoord[1]))
      ? Number(lmCoord[1])
      : rbCoord && Number.isFinite(Number(rbCoord[1]))
      ? Number(rbCoord[1])
      : null;

    let tilePolygonUpgraded = false;
    const tryUpgradeFromTiles = () => {
      if (navSeq !== this._selectNavSeq || tilePolygonUpgraded || this.useCanvasFallback || !this.map) {
        return false;
      }
      const tileLayers = [
        "curated-overrides-extrusion",
        "curated-overrides-fill",
        ...(this.buildingFillLayerIds || []),
        ...(this.buildingExtrusionLayerIds || []),
      ].filter((id) => this.map.getLayer(id));
      if (!tileLayers.length) return false;

      const renderedAfterFly = this.map.queryRenderedFeatures({ layers: tileLayers });
      let tileBld = renderedAfterFly.find(matchesCandidate) || null;

      if (!tileBld && candidateHcads.size > 0 && Array.isArray(this.shardSourceIds)) {
        const hcadArr = Array.from(candidateHcads);
        for (const srcId of this.shardSourceIds) {
          try {
            const hits = this.map.querySourceFeatures(srcId, {
              sourceLayer: "buildings",
              filter: ["in", ["get", "hcad_num"], ["literal", hcadArr]],
            });
            const foundHit = hits.find(matchesCandidate) || hits[0] || null;
            if (foundHit) {
              tileBld = foundHit;
              break;
            }
          } catch (_e) {}
        }
      }

      if (!tileBld && Number.isFinite(effLng) && Number.isFinite(effLat)) {
        const pt = this.map.project([effLng, effLat]);
        const bbox = [
          [pt.x - 24, pt.y - 24],
          [pt.x + 24, pt.y + 24],
        ];
        const nearHits = this.map.queryRenderedFeatures(bbox, { layers: tileLayers });
        tileBld = nearHits.find(matchesCandidate) || nearHits[0] || null;
      }

      if (tileBld) {
        tilePolygonUpgraded = true;
        const baseTileProps = { ...(tileBld.properties || {}) };
        if (!baseTileProps.address && (addressQuery || matchedAlert?.address || matchedPlat?.address)) {
          baseTileProps.address = addressQuery || matchedAlert?.address || matchedPlat?.address;
        }
        if (!baseTileProps.year_built && (fallbackYearBuilt || matchedAlert?.year_built || matchedPlat?.year_built)) {
          const yr = Number(fallbackYearBuilt || matchedAlert?.year_built || matchedPlat?.year_built) || 0;
          if (yr > 0) {
            baseTileProps.year_built = yr;
            baseTileProps.decade = Math.floor(yr / 10) * 10;
          }
        }
        this.highlightAndInspectFeature(
          applyOverrideToProperties(baseTileProps, this.curatedOverrides),
          tileBld.geometry || null
        );
        return true;
      }
      return false;
    };

    const schedulePostFlightTileUpgrade = () => {
      if (this.useCanvasFallback || !this.map) return;
      this.map.once("idle", () => {
        tryUpgradeFromTiles();
      });
      for (const delayMs of [350, 950, 1800]) {
        setTimeout(() => {
          tryUpgradeFromTiles();
        }, delayMs);
      }
    };

    if (!match) {
      const effectiveHcad =
        matchedAlert?.hcad_num ||
        matchedPlat?.hcad_num ||
        hcadNum ||
        Array.from(candidateHcads)[0] ||
        "";
      const effectiveAddr =
        matchedAlert?.address ||
        matchedPlat?.address ||
        matchedHahc?.address ||
        matchedLm?.address ||
        matchedLm?.USER_ADDRESS ||
        matchedRb?.modern_address ||
        addressQuery ||
        (effectiveHcad ? `HCAD ${effectiveHcad}` : "");
      const effectiveYear =
        Number(
          matchedAlert?.year_built ||
            matchedPlat?.year_built ||
            matchedLm?.year_built ||
            fallbackYearBuilt
        ) || 0;

      if (matchedAlert || matchedPlat || effectiveHcad || effectiveAddr) {
        const initialProps = applyOverrideToProperties(
          {
            id:
              matchedAlert?.building_id ||
              matchedPlat?.building_id ||
              featureId ||
              (effectiveHcad ? `${effectiveHcad}#b1` : `addr_${effectiveAddr}`),
            building_id: matchedAlert?.building_id || matchedPlat?.building_id || featureId || "",
            hcad_num: effectiveHcad,
            address: effectiveAddr,
            building_name:
              matchedAlert?.building_name ||
              matchedAlert?.landmark_name ||
              matchedPlat?.building_name ||
              matchedLm?.name ||
              matchedLm?.USER_LANDMARK ||
              matchedRb?.name ||
              "",
            landmark_name: matchedAlert?.landmark_name || matchedLm?.name || matchedLm?.USER_LANDMARK || "",
            year_built: effectiveYear,
            decade: effectiveYear ? Math.floor(effectiveYear / 10) * 10 : 0,
            yr_remodel: Number(matchedAlert?.yr_remodel) || 0,
            style: matchedAlert?.style || matchedLm?.style || "",
            architect: matchedAlert?.architect || matchedLm?.architect || "",
            neighborhood: matchedAlert?.neighborhood || matchedPlat?.neighborhood || "",
            historic_district:
              matchedAlert?.historic_district ||
              matchedHahc?.historic_district ||
              matchedLm?.historic_district ||
              "",
            good_brick_summary: matchedAlert?.good_brick_summary || "",
            owner: matchedAlert?.owner || matchedPlat?.developer || "",
            bld_area: Number(matchedAlert?.bld_area) || 0,
            land_area: Number(matchedAlert?.land_area) || 0,
            stories: Number(matchedAlert?.stories) || 1,
            subdivision: matchedAlert?.subdivision || matchedPlat?.subdivision || "",
          },
          this.curatedOverrides
        );
        if (flyTo && Number.isFinite(effLng) && Number.isFinite(effLat)) {
          this.flyToLocation({
            lng: effLng,
            lat: effLat,
            zoom: 17.6,
          });
        }
        const initialGeom =
          Number.isFinite(effLng) && Number.isFinite(effLat)
            ? { type: "Point", coordinates: [effLng, effLat] }
            : null;
        this.highlightAndInspectFeature(initialProps, initialGeom);
        schedulePostFlightTileUpgrade();
      }

      if (effectiveHcad) {
        fetchHcadLiveRecord(effectiveHcad)
          .then((rec) => {
            if (navSeq !== this._selectNavSeq || !rec || tilePolygonUpgraded) return;
            const recYear = rec.yearImpr || effectiveYear;
            const synthProps = applyOverrideToProperties(
              {
                id: matchedAlert?.building_id || matchedPlat?.building_id || `hcad_${rec.hcadNum}`,
                hcad_num: rec.hcadNum,
                address: rec.address || effectiveAddr || `HCAD ${rec.hcadNum}`,
                year_built: recYear,
                decade: recYear ? Math.floor(recYear / 10) * 10 : 0,
                bld_sqft: rec.bldgSqft || Number(matchedAlert?.bld_area) || 0,
                land_sqft: rec.lotAreaSqft || Number(matchedAlert?.land_area) || 0,
                owner: rec.owner || matchedAlert?.owner || "",
                subdivision: rec.subdivision || matchedAlert?.subdivision || matchedPlat?.subdivision || "",
                neighborhood: matchedAlert?.neighborhood || matchedPlat?.neighborhood || "",
                use_category:
                  rec.stateClass && rec.stateClass.startsWith("A")
                    ? "Single-Family Residential"
                    : rec.stateClass && rec.stateClass.startsWith("B")
                    ? "Multi-Family Residential"
                    : rec.stateClass && rec.stateClass.startsWith("F")
                    ? "Commercial"
                    : "Structure",
              },
              this.curatedOverrides
            );
            if (flyTo && !Number.isFinite(effLng) && Array.isArray(rec.centroid)) {
              this.flyToLocation({
                lng: rec.centroid[0],
                lat: rec.centroid[1],
                zoom: 17.2,
              });
            }
            this.highlightAndInspectFeature(synthProps, rec.geometry || null);
            schedulePostFlightTileUpgrade();
          })
          .catch(() => {});
        return true;
      }
      if (matchedAlert || matchedPlat || effectiveAddr || hasCoords) {
        schedulePostFlightTileUpgrade();
        return true;
      }
      return false;
    }

    const mergedProps = applyOverrideToProperties(match.properties || {}, this.curatedOverrides);
    if (gbMatch && gbMatch.properties) {
      mergedProps.good_brick_awards =
        gbMatch.properties.good_brick_awards || mergedProps.good_brick_awards || null;
      mergedProps.good_brick_summary =
        gbMatch.properties.good_brick_summary || mergedProps.good_brick_summary || "";
      mergedProps.good_brick_years =
        gbMatch.properties.good_brick_years || mergedProps.good_brick_years || "";
      if (!mergedProps.landmark_name && gbMatch.properties.landmark_name) {
        mergedProps.landmark_name = gbMatch.properties.landmark_name;
      }
    }

    if (flyTo && match.geometry) {
      let lng = null;
      let lat = null;
      if (match.geometry.type === "Point" && Array.isArray(match.geometry.coordinates)) {
        [lng, lat] = match.geometry.coordinates;
      } else if (match.geometry.type === "Polygon" && match.geometry.coordinates?.[0]?.[0]) {
        [lng, lat] = match.geometry.coordinates[0][0];
      } else if (
        match.geometry.type === "MultiPolygon" &&
        match.geometry.coordinates?.[0]?.[0]?.[0]
      ) {
        [lng, lat] = match.geometry.coordinates[0][0][0];
      }
      if (Number.isFinite(lng) && Number.isFinite(lat)) {
        this.flyToLocation({ lng, lat, zoom: 17.2 });
      }
    }

    this.highlightAndInspectFeature(mergedProps, match.geometry || null);
    if (!match.geometry || match.geometry.type === "Point") {
      schedulePostFlightTileUpgrade();
    }
    return true;
  }

  setTourFocusMode(mode = "all") {
    const valid = mode === "landmarks" || mode === "tour_only" ? mode : "all";
    this.tourFocusMode = valid;
    this.syncWithState(this.filterStore.getState());
    this.computeViewportHistogram();
  }

  getTourFocusMode() {
    return this.tourFocusMode || "all";
  }

  setTourRoute(tour = null, activeStopIndex = -1) {
    this.activeTour = tour || null;
    this.activeTourStopIndex = Number.isInteger(activeStopIndex) ? activeStopIndex : -1;
    if (!this.activeTour && this.tourFocusMode !== "all") {
      this.tourFocusMode = "all";
    }

    if (Array.isArray(this.tourStopMarkers)) {
      for (const m of this.tourStopMarkers) {
        try {
          m.remove();
        } catch {}
      }
      this.tourStopMarkers = [];
    }

    const stops = Array.isArray(tour?.stops) ? tour.stops : [];

    if (this.useCanvasFallback) {
      this._renderCanvas2D();
      return;
    }

    if (!this.map) return;

    this.syncWithState(this.filterStore.getState());

    const routeSrc = this.map.getSource("tour-route-src");
    if (routeSrc) {
      if (stops.length > 1) {
        const routeCoords =
          Array.isArray(tour?.routeCoords) && tour.routeCoords.length > 1
            ? tour.routeCoords
            : stops.map((s) => [s.lng, s.lat]);
        const features = [
          {
            type: "Feature",
            geometry: {
              type: "LineString",
              coordinates: routeCoords,
            },
            properties: {
              tour_id: tour.id || "",
              feature_type: "route",
            },
          },
        ];
        for (let i = 0; i < stops.length; i++) {
          const s = stops[i];
          if (Number.isFinite(s.street_lng) && Number.isFinite(s.street_lat)) {
            features.push({
              type: "Feature",
              geometry: {
                type: "LineString",
                coordinates: [
                  [s.street_lng, s.street_lat],
                  [s.lng, s.lat],
                ],
              },
              properties: {
                tour_id: tour.id || "",
                stop_index: i,
                feature_type: "connector",
              },
            });
          }
        }
        routeSrc.setData({
          type: "FeatureCollection",
          features,
        });
      } else {
        routeSrc.setData({ type: "FeatureCollection", features: [] });
      }
    }

    if (stops.length > 0 && window.maplibregl && window.maplibregl.Marker) {
      for (let i = 0; i < stops.length; i++) {
        const s = stops[i];
        const isActive = i === this.activeTourStopIndex;
        const pinBtn = document.createElement("button");
        pinBtn.type = "button";
        pinBtn.className = `tour-map-pin mono${isActive ? " active" : ""}`;
        pinBtn.title = `Stop ${i + 1}: ${s.title} (${s.year})`;
        pinBtn.setAttribute("aria-label", `Stop ${i + 1}: ${s.title}`);
        pinBtn.innerHTML = `<span class="tour-map-pin-num">${i + 1}</span>`;
        pinBtn.addEventListener("click", (e) => {
          e.preventDefault();
          e.stopPropagation();
          if (typeof this.onSelectTourStop === "function") {
            this.onSelectTourStop(i);
          }
        });
        const marker = new window.maplibregl.Marker({
          element: pinBtn,
          anchor: "center",
        })
          .setLngLat([s.lng, s.lat])
          .addTo(this.map);
        this.tourStopMarkers.push(marker);
      }
    }
  }

  clearPortfolioWorks() {
    this.portfolioWorks = [];
    this.activePortfolioWorkIndex = -1;
    if (Array.isArray(this.portfolioMarkers)) {
      for (const m of this.portfolioMarkers) {
        try {
          m.remove();
        } catch (_e) {}
      }
    }
    this.portfolioMarkers = [];
    if (this.useCanvasFallback) {
      this._renderCanvas2D();
      return;
    }
    if (this.map) {
      const pSrc = this.map.getSource("portfolio-works-src");
      if (pSrc) {
        pSrc.setData({ type: "FeatureCollection", features: [] });
      }
    }
  }

  setPortfolioWorks(works = [], activeIndex = -1, { fit = false } = {}) {
    if (Array.isArray(this.portfolioMarkers)) {
      for (const m of this.portfolioMarkers) {
        try {
          m.remove();
        } catch (_e) {}
      }
    }
    this.portfolioMarkers = [];
    const validWorks = Array.isArray(works)
      ? works.filter((w) => Number.isFinite(Number(w?.lng)) && Number.isFinite(Number(w?.lat)))
      : [];
    this.portfolioWorks = validWorks;
    this.activePortfolioWorkIndex = activeIndex;

    if (this.useCanvasFallback) {
      if (fit && validWorks.length > 0 && this.canvasState) {
        let minLng = Infinity;
        let maxLng = -Infinity;
        let minLat = Infinity;
        let maxLat = -Infinity;
        for (const w of validWorks) {
          minLng = Math.min(minLng, Number(w.lng));
          maxLng = Math.max(maxLng, Number(w.lng));
          minLat = Math.min(minLat, Number(w.lat));
          maxLat = Math.max(maxLat, Number(w.lat));
        }
        this.canvasState.lng = (minLng + maxLng) / 2;
        this.canvasState.lat = (minLat + maxLat) / 2;
        const span = Math.max(maxLng - minLng, maxLat - minLat);
        this.canvasState.zoom = span < 0.015 ? 15.2 : span < 0.06 ? 13.4 : span < 0.16 ? 11.9 : 10.8;
      }
      this._renderCanvas2D();
      return;
    }

    if (!this.map) return;
    const pSrc = this.map.getSource("portfolio-works-src");
    if (pSrc) {
      pSrc.setData({
        type: "FeatureCollection",
        features: validWorks.map((w, idx) => ({
          type: "Feature",
          geometry: {
            type: "Point",
            coordinates: [Number(w.lng), Number(w.lat)],
          },
          properties: {
            id: String(w.id || idx),
            name: w.title || w.address || "Portfolio Work",
            address: w.address || "",
            architect: w.architect || "",
            style: w.style || "",
            year_built: Number(w.yearBuilt) || 0,
            year_demolished: Number(w.yearDemolished) || 0,
            is_lost: Boolean(w.isLost),
            is_active: idx === activeIndex,
            portfolio_index: idx,
          },
        })),
      });
    }

    if (validWorks.length > 0 && validWorks.length <= 60 && window.maplibregl && window.maplibregl.Marker) {
      for (let i = 0; i < validWorks.length; i++) {
        const w = validWorks[i];
        const isActive = i === activeIndex;
        const pinBtn = document.createElement("button");
        pinBtn.type = "button";
        pinBtn.className = `portfolio-map-pin mono${w.isLost ? " is-lost" : ""}${
          isActive ? " active" : ""
        }`;
        const yrText = w.isLost
          ? `${w.yearBuilt || "?"}-${w.yearDemolished || "?"}`
          : w.yearBuilt || "Undated";
        pinBtn.title = `${i + 1}. ${w.title} (${yrText})${w.isLost ? " [Lost Houston]" : ""}`;
        pinBtn.setAttribute("aria-label", pinBtn.title);
        pinBtn.innerHTML = `<span class="portfolio-map-pin-num">${w.isLost ? `†${i + 1}` : i + 1}</span>`;
        pinBtn.addEventListener("click", (e) => {
          e.preventDefault();
          e.stopPropagation();
          if (typeof this.onSelectPortfolioWork === "function") {
            this.onSelectPortfolioWork(i);
          }
        });
        const marker = new window.maplibregl.Marker({
          element: pinBtn,
          anchor: "center",
        })
          .setLngLat([Number(w.lng), Number(w.lat)])
          .addTo(this.map);
        this.portfolioMarkers.push(marker);
      }
    }

    if (fit && validWorks.length > 0) {
      if (validWorks.length === 1) {
        this.flyToLocation({
          lng: Number(validWorks[0].lng),
          lat: Number(validWorks[0].lat),
          zoom: 16.5,
        });
      } else {
        let minLng = Infinity;
        let maxLng = -Infinity;
        let minLat = Infinity;
        let maxLat = -Infinity;
        for (const w of validWorks) {
          minLng = Math.min(minLng, Number(w.lng));
          maxLng = Math.max(maxLng, Number(w.lng));
          minLat = Math.min(minLat, Number(w.lat));
          maxLat = Math.max(maxLat, Number(w.lat));
        }
        try {
          this.map.fitBounds(
            [
              [minLng, minLat],
              [maxLng, maxLat],
            ],
            {
              padding: { top: 84, right: 380, bottom: 260, left: 84 },
              maxZoom: 15.8,
              duration: 1050,
            }
          );
        } catch (_e) {}
      }
    }
  }

  flyToLocation(arg1, arg2, arg3 = 16.5) {
    let lng;
    let lat;
    let zoom = 16.5;
    let pitch = null;
    let hcadNum = "";
    let featureId = "";
    let addressQuery = "";
    let fallbackYearBuilt = 0;
    let extraCandidateHcads = [];
    if (arg1 && typeof arg1 === "object") {
      lng = Number(arg1.lng);
      lat = Number(arg1.lat);
      if (Number.isFinite(Number(arg1.zoom))) zoom = Number(arg1.zoom);
      if (arg1.pitch !== undefined && arg1.pitch !== null) pitch = Number(arg1.pitch);
      hcadNum = arg1.hcadNum || "";
      featureId = arg1.featureId || "";
      addressQuery = arg1.addressQuery || "";
      fallbackYearBuilt = Number(arg1.fallbackYearBuilt) || 0;
      extraCandidateHcads = Array.isArray(arg1.extraCandidateHcads) ? arg1.extraCandidateHcads : [];
    } else {
      lng = Number(arg1);
      lat = Number(arg2);
      if (Number.isFinite(Number(arg3))) zoom = Number(arg3);
    }
    if (!Number.isFinite(lng) || !Number.isFinite(lat)) return;

    if (this.useCanvasFallback && this.canvasState) {
      this.canvasState.lng = lng;
      this.canvasState.lat = lat;
      this.canvasState.zoom = zoom;
      if (pitch !== null) this.canvasState.pitch = pitch;
      this._renderCanvas2D();
      this.computeViewportHistogram();
    } else if (this.map) {
      const state = this.filterStore.getState();
      this.map.flyTo({
        center: [lng, lat],
        zoom,
        pitch: pitch !== null ? pitch : state.extrude3D ? 50 : 0,
        duration: 1100,
        essential: true,
      });
    }

    if (featureId || hcadNum || addressQuery) {
      this.selectFeatureByIdOrHcad({
        hcadNum,
        featureId,
        addressQuery,
        fallbackYearBuilt,
        extraCandidateHcads,
        targetLng: lng,
        targetLat: lat,
        flyTo: false,
      });
    }
  }

  _resolveSelectedPolygonGeometry(mergedProps, candidateGeom = null) {
    const isPoly = (g) => g && (g.type === "Polygon" || g.type === "MultiPolygon");
    if (isPoly(candidateGeom)) return candidateGeom;

    const targetBldId = String(mergedProps?.building_id || mergedProps?.id || "").trim();
    const targetHcad = String(mergedProps?.hcad_num || "").trim();

    const searchCollection = (features) => {
      if (!Array.isArray(features) || !features.length) return null;
      if (targetBldId) {
        const byId = features.find((f) => {
          const p = f.properties || {};
          return (p.id === targetBldId || p.building_id === targetBldId) && isPoly(f.geometry);
        });
        if (byId) return byId.geometry;
      }
      if (targetHcad) {
        const byHcad = features.find(
          (f) => f.properties && f.properties.hcad_num === targetHcad && isPoly(f.geometry)
        );
        if (byHcad) return byHcad.geometry;
      }
      return null;
    };

    const fromOverrides = searchCollection(this.overridesFC?.features);
    if (fromOverrides) return fromOverrides;

    if (!this.useCanvasFallback && this.map) {
      const polyLayers = [
        "curated-overrides-extrusion",
        "curated-overrides-fill",
        ...(this.buildingExtrusionLayerIds || []),
        ...(this.buildingFillLayerIds || []),
      ].filter((id) => this.map.getLayer(id));
      if (polyLayers.length > 0) {
        try {
          const rendered = this.map.queryRenderedFeatures({ layers: polyLayers });
          const fromRendered = searchCollection(rendered);
          if (fromRendered) return fromRendered;
        } catch (_e) {}
      }
      if (targetHcad && Array.isArray(this.shardSourceIds)) {
        for (const srcId of this.shardSourceIds) {
          try {
            const srcHits = this.map.querySourceFeatures(srcId, {
              sourceLayer: "buildings",
              filter: ["==", ["get", "hcad_num"], targetHcad],
            });
            const fromSrc = searchCollection(srcHits);
            if (fromSrc) return fromSrc;
          } catch (_e) {}
        }
      }
    }

    const fromBuildings = searchCollection(this.buildingsData);
    if (fromBuildings) return fromBuildings;

    return null;
  }

  _build3DSelectionWireframeFeatures(geom, props, state) {
    if (!geom || (geom.type !== "Polygon" && geom.type !== "MultiPolygon")) return [];

    const baseHeight = Number(props?.height_m) || 4.5;
    const yearTie = ((Number(props?.year_built) || 1900) % 97) * 0.0003;
    const H = Math.max(2.5, baseHeight + yearTie);
    const bodyColor = evaluateFeatureColor(props || {}, state.colorMode, state.paletteStyle);
    const wireColor = "#FDE047";

    const features = [
      // 1. Selected building's own 3D body volume in the shared fill-extrusion depth pass
      // so the building's front walls and roof occlude its own back base edges and back corners.
      {
        type: "Feature",
        geometry: geom,
        properties: {
          wire_type: "body",
          wire_color: bodyColor,
          wire_base: 0,
          wire_height: Number((H + 0.03).toFixed(3)),
        },
      },
    ];

    const polyCoordsList =
      geom.type === "Polygon" ? [geom.coordinates] : geom.coordinates || [];

    const allLngs = [];
    const allLats = [];

    for (const polyRings of polyCoordsList) {
      if (!Array.isArray(polyRings)) continue;
      for (let ringIdx = 0; ringIdx < polyRings.length; ringIdx++) {
        const rawRing = polyRings[ringIdx];
        if (!Array.isArray(rawRing) || rawRing.length < 4) continue;

        const refLng = Number(rawRing[0][0]);
        const refLat = Number(rawRing[0][1]);
        if (!Number.isFinite(refLng) || !Number.isFinite(refLat)) continue;

        const metersPerDegLat = 111320.0;
        const metersPerDegLng = 111320.0 * Math.cos((refLat * Math.PI) / 180.0);

        const pts = [];
        for (let i = 0; i < rawRing.length; i++) {
          const lng = Number(rawRing[i][0]);
          const lat = Number(rawRing[i][1]);
          if (!Number.isFinite(lng) || !Number.isFinite(lat)) continue;
          allLngs.push(lng);
          allLats.push(lat);
          const x = (lng - refLng) * metersPerDegLng;
          const y = (lat - refLat) * metersPerDegLat;
          if (pts.length > 0) {
            const prev = pts[pts.length - 1];
            if (Math.hypot(x - prev.x, y - prev.y) < 0.08) continue;
          }
          pts.push({ x, y, lng, lat });
        }
        if (pts.length > 1) {
          const first = pts[0];
          const last = pts[pts.length - 1];
          if (Math.hypot(first.x - last.x, first.y - last.y) < 0.08) {
            pts.pop();
          }
        }
        const n = pts.length;
        if (n < 3) continue;

        let signedArea2 = 0;
        for (let i = 0; i < n; i++) {
          const p0 = pts[i];
          const p1 = pts[(i + 1) % n];
          signedArea2 += p0.x * p1.y - p1.x * p0.y;
        }
        // For exterior rings (ringIdx === 0), outward is away from polygon interior.
        // For courtyard holes (ringIdx > 0), outward into the courtyard is toward hole center.
        const orientSign = (signedArea2 >= 0 ? 1 : -1) * (ringIdx === 0 ? 1 : -1);

        const edgeNormals = [];
        const edgeDirs = [];
        const edgeLens = [];
        for (let i = 0; i < n; i++) {
          const p0 = pts[i];
          const p1 = pts[(i + 1) % n];
          const dx = p1.x - p0.x;
          const dy = p1.y - p0.y;
          const len = Math.hypot(dx, dy) || 1e-6;
          const ux = dx / len;
          const uy = dy / len;
          edgeDirs.push({ ux, uy });
          edgeLens.push(len);
          // For CCW ring (signedArea2 > 0), right-hand normal (+uy, -ux) points outward
          edgeNormals.push({
            nx: orientSign * uy,
            ny: -orientSign * ux,
          });
        }

        const miters = [];
        for (let i = 0; i < n; i++) {
          const nPrev = edgeNormals[(i - 1 + n) % n];
          const nCurr = edgeNormals[i];
          let mx = nPrev.nx + nCurr.nx;
          let my = nPrev.ny + nCurr.ny;
          const mLen = Math.hypot(mx, my);
          if (mLen < 1e-4) {
            miters.push({ mx: nCurr.nx, my: nCurr.ny });
          } else {
            mx /= mLen;
            my /= mLen;
            const dot = mx * nCurr.nx + my * nCurr.ny;
            const scale = Math.min(1.85, 1.0 / Math.max(0.35, Math.abs(dot)));
            miters.push({ mx: mx * scale, my: my * scale });
          }
        }

        const toLngLat = (xMeters, yMeters) => [
          Number((refLng + xMeters / metersPerDegLng).toFixed(7)),
          Number((refLat + yMeters / metersPerDegLat).toFixed(7)),
        ];

        const roofBase = Number(Math.max(0.25, H - 0.65).toFixed(3));
        const roofTop = Number((H + 0.36).toFixed(3));
        const baseTop = Number(Math.min(1.15, Math.max(0.45, H * 0.06)).toFixed(3));

        const roofIn = -0.35;
        const roofOut = 0.72;
        const baseIn = 0.06;
        const baseOut = 0.92;

        for (let i = 0; i < n; i++) {
          const p0 = pts[i];
          const p1 = pts[(i + 1) % n];
          const m0 = miters[i];
          const m1 = miters[(i + 1) % n];

          // 2. 3D Roofline Collar Quad around the top of the extruded building (z = H)
          const r0In = toLngLat(p0.x + m0.mx * roofIn, p0.y + m0.my * roofIn);
          const r1In = toLngLat(p1.x + m1.mx * roofIn, p1.y + m1.my * roofIn);
          const r1Out = toLngLat(p1.x + m1.mx * roofOut, p1.y + m1.my * roofOut);
          const r0Out = toLngLat(p0.x + m0.mx * roofOut, p0.y + m0.my * roofOut);

          features.push({
            type: "Feature",
            geometry: {
              type: "Polygon",
              coordinates: [[r0In, r1In, r1Out, r0Out, r0In]],
            },
            properties: {
              wire_type: "roof_collar",
              wire_color: wireColor,
              wire_base: roofBase,
              wire_height: roofTop,
            },
          });

          // 3. 3D Ground-Base Collar Quad (z = 0..baseTop, occluded behind building body)
          if (ringIdx === 0) {
            const b0In = toLngLat(p0.x + m0.mx * baseIn, p0.y + m0.my * baseIn);
            const b1In = toLngLat(p1.x + m1.mx * baseIn, p1.y + m1.my * baseIn);
            const b1Out = toLngLat(p1.x + m1.mx * baseOut, p1.y + m1.my * baseOut);
            const b0Out = toLngLat(p0.x + m0.mx * baseOut, p0.y + m0.my * baseOut);

            features.push({
              type: "Feature",
              geometry: {
                type: "Polygon",
                coordinates: [[b0In, b1In, b1Out, b0Out, b0In]],
              },
              properties: {
                wire_type: "base_collar",
                wire_color: wireColor,
                wire_base: 0,
                wire_height: baseTop,
              },
            });
          }
        }

        // 4. 3D Vertical Corner Edge Ribs (z = 0..H, occluded on back corners by building body)
        if (ringIdx === 0) {
          let lastRibPt = null;
          const ribHalf = 0.26;
          for (let i = 0; i < n; i++) {
            const uPrev = edgeDirs[(i - 1 + n) % n];
            const uCurr = edgeDirs[i];
            const lenPrev = edgeLens[(i - 1 + n) % n];
            const lenCurr = edgeLens[i];
            const dot = uPrev.ux * uCurr.ux + uPrev.uy * uCurr.uy;
            if (dot > 0.94 || (lenPrev < 1.6 && lenCurr < 1.6)) continue;

            const p = pts[i];
            if (lastRibPt && Math.hypot(p.x - lastRibPt.x, p.y - lastRibPt.y) < 2.2) {
              continue;
            }
            lastRibPt = p;
            const m = miters[i];
            const cx = p.x + m.mx * 0.36;
            const cy = p.y + m.my * 0.36;

            const c0 = toLngLat(cx - ribHalf, cy - ribHalf);
            const c1 = toLngLat(cx + ribHalf, cy - ribHalf);
            const c2 = toLngLat(cx + ribHalf, cy + ribHalf);
            const c3 = toLngLat(cx - ribHalf, cy + ribHalf);

            features.push({
              type: "Feature",
              geometry: {
                type: "Polygon",
                coordinates: [[c0, c1, c2, c3, c0]],
              },
              properties: {
                wire_type: "corner_rib",
                wire_color: wireColor,
                wire_base: 0,
                wire_height: roofTop,
              },
            });
          }
        }
      }
    }

    // 5. Nearby 3D Building Occluders in dense areas (e.g., Downtown skyscrapers in front of
    // the selected building): include their 3D volumes in the same fill-extrusion depth buffer
    // so taller foreground structures naturally occlude the selected building's 3D wireframe.
    if (this.map && allLngs.length > 0) {
      try {
        let minScreenX = Infinity;
        let maxScreenX = -Infinity;
        let minScreenY = Infinity;
        let maxScreenY = -Infinity;
        for (let i = 0; i < allLngs.length; i++) {
          const pt = this.map.project([allLngs[i], allLats[i]]);
          if (pt.x < minScreenX) minScreenX = pt.x;
          if (pt.x > maxScreenX) maxScreenX = pt.x;
          if (pt.y < minScreenY) minScreenY = pt.y;
          if (pt.y > maxScreenY) maxScreenY = pt.y;
        }
        const padX = 160;
        const padY = 220;
        const queryBox = [
          [minScreenX - padX, minScreenY - padY],
          [maxScreenX + padX, maxScreenY + padY],
        ];
        const extLayers = [
          "curated-overrides-extrusion",
          ...(this.buildingExtrusionLayerIds || []),
        ].filter((id) => this.map.getLayer(id));

        if (extLayers.length > 0) {
          const nearby = this.map.queryRenderedFeatures(queryBox, { layers: extLayers });
          const selId = String(props?.id || "").trim();
          const selBldId = String(props?.building_id || "").trim();
          const selHcad = String(props?.hcad_num || "").trim();
          const seenKeys = new Set();

          for (const nf of nearby) {
            if (!nf.geometry || (nf.geometry.type !== "Polygon" && nf.geometry.type !== "MultiPolygon")) {
              continue;
            }
            const np = applyOverrideToProperties(nf.properties || {}, this.curatedOverrides);
            const nId = String(np.id || "").trim();
            const nBldId = String(np.building_id || "").trim();
            const nHcad = String(np.hcad_num || "").trim();

            if (
              (selId && (nId === selId || nBldId === selId)) ||
              (selBldId && (nId === selBldId || nBldId === selBldId)) ||
              (!selBldId && selHcad && nHcad === selHcad && !nBldId)
            ) {
              continue;
            }

            const firstCoord =
              nf.geometry.type === "Polygon"
                ? nf.geometry.coordinates?.[0]?.[0]
                : nf.geometry.coordinates?.[0]?.[0]?.[0];
            const dedupKey = firstCoord
              ? `${nId || nHcad}:${firstCoord[0].toFixed(5)},${firstCoord[1].toFixed(5)}`
              : `${nId || nHcad}:${seenKeys.size}`;
            if (seenKeys.has(dedupKey)) continue;
            seenKeys.add(dedupKey);

            const nBaseH = Number(np.height_m) || 4.5;
            const nTie = ((Number(np.year_built) || 1900) % 97) * 0.0003;
            const nH = Math.max(2.5, nBaseH + nTie);
            const nColor = evaluateFeatureColor(np, state.colorMode, state.paletteStyle);

            features.push({
              type: "Feature",
              geometry: nf.geometry,
              properties: {
                wire_type: "occluder",
                wire_color: nColor,
                wire_base: 0,
                wire_height: Number((nH + 0.02).toFixed(3)),
              },
            });
            if (seenKeys.size >= 45) break;
          }
        }
      } catch (_e) {}
    }

    return features;
  }

  _updateSelectedFeature3DSource(stateOverride = null) {
    if (!this.map) return;
    const sel3DSrc = this.map.getSource("selected-feature-3d-src");
    if (!sel3DSrc) return;

    const state = stateOverride || this.filterStore.getState();
    if (
      !state.extrude3D ||
      !this.selectedFeatureProps ||
      !this.selectedFeatureGeometry ||
      !featureMatchesFilter(this.selectedFeatureProps, state)
    ) {
      sel3DSrc.setData({ type: "FeatureCollection", features: [] });
      return;
    }

    const wireFeatures = this._build3DSelectionWireframeFeatures(
      this.selectedFeatureGeometry,
      this.selectedFeatureProps,
      state
    );
    sel3DSrc.setData({
      type: "FeatureCollection",
      features: wireFeatures,
    });
  }

  _refreshSelectionAfterViewportChange() {
    if (!this.map || !this.selectedFeatureProps) return;
    if (!this.selectedFeatureGeometry) {
      const resolved = this._resolveSelectedPolygonGeometry(this.selectedFeatureProps, null);
      if (resolved) {
        this.selectedFeatureGeometry = resolved;
        const selSrc = this.map.getSource("selected-feature-src");
        if (selSrc) {
          selSrc.setData({
            type: "FeatureCollection",
            features: [
              {
                type: "Feature",
                geometry: resolved,
                properties: this.selectedFeatureProps,
              },
            ],
          });
        }
      }
    }
    const state = this.filterStore.getState();
    if (state.extrude3D && this.selectedFeatureGeometry) {
      this._updateSelectedFeature3DSource(state);
    }
  }

  _computeGeometryBBoxAndCentroid(geom) {
    if (!geom || !geom.coordinates) return null;
    if (geom.type === "Point" && Array.isArray(geom.coordinates)) {
      const lng = Number(geom.coordinates[0]);
      const lat = Number(geom.coordinates[1]);
      if (!Number.isFinite(lng) || !Number.isFinite(lat)) return null;
      return {
        bbox: [lng, lat, lng, lat],
        lng,
        lat,
      };
    }
    let minLng = Infinity;
    let minLat = Infinity;
    let maxLng = -Infinity;
    let maxLat = -Infinity;
    let sumLng = 0;
    let sumLat = 0;
    let count = 0;

    const visitRing = (ring) => {
      if (!Array.isArray(ring)) return;
      for (const pt of ring) {
        if (!Array.isArray(pt) || pt.length < 2) continue;
        const lng = Number(pt[0]);
        const lat = Number(pt[1]);
        if (!Number.isFinite(lng) || !Number.isFinite(lat)) continue;
        if (lng < minLng) minLng = lng;
        if (lng > maxLng) maxLng = lng;
        if (lat < minLat) minLat = lat;
        if (lat > maxLat) maxLat = lat;
        sumLng += lng;
        sumLat += lat;
        count += 1;
      }
    };

    if (geom.type === "Polygon" && Array.isArray(geom.coordinates)) {
      visitRing(geom.coordinates[0]);
    } else if (geom.type === "MultiPolygon" && Array.isArray(geom.coordinates)) {
      for (const poly of geom.coordinates) {
        if (Array.isArray(poly) && poly[0]) {
          visitRing(poly[0]);
        }
      }
    } else if (geom.type === "LineString" && Array.isArray(geom.coordinates)) {
      visitRing(geom.coordinates);
    } else if (geom.type === "MultiLineString" && Array.isArray(geom.coordinates)) {
      for (const line of geom.coordinates) {
        if (Array.isArray(line)) {
          visitRing(line);
        }
      }
    }

    if (count === 0 || !Number.isFinite(minLng)) return null;
    return {
      bbox: [minLng, minLat, maxLng, maxLat],
      lng: sumLng / count,
      lat: sumLat / count,
    };
  }

  _buildBoundarySpatialIndex() {
    this.boundarySpatialIndex = [];
    const indexLayer = (fc, overlayKey, allowNonPolygon = false) => {
      if (!fc || !Array.isArray(fc.features)) return;
      for (let idx = 0; idx < fc.features.length; idx++) {
        const feat = fc.features[idx];
        const geom = feat?.geometry;
        if (!geom) continue;
        const isPoly = geom.type === "Polygon" || geom.type === "MultiPolygon";
        if (!isPoly && !allowNonPolygon) continue;
        const meta = this._computeGeometryBBoxAndCentroid(geom);
        if (!meta) continue;
        const rawProps = feat.properties || {};
        const fallbackId = `${overlayKey}_${idx}`;
        const propArea = Number(rawProps.area_deg2);
        this.boundarySpatialIndex.push({
          overlayKey,
          feature: feat,
          props: {
            ...rawProps,
            id: rawProps.id || fallbackId,
            overlay_layer: rawProps.overlay_layer || overlayKey,
          },
          bbox: meta.bbox,
          centroid: [meta.lng, meta.lat],
          areaDeg2:
            Number.isFinite(propArea) && propArea > 0
              ? propArea
              : Math.max(1e-8, meta.bbox[2] - meta.bbox[0]) *
                Math.max(1e-8, meta.bbox[3] - meta.bbox[1]),
        });
      }
    };
    indexLayer(this.overlaysData?.lost_houston, "lost_houston");
    indexLayer(this.overlaysData?.platted_subdivisions, "platted_subdivisions");
    indexLayer(this.overlaysData?.land_use_protections, "land_use_protections");
    indexLayer(this.overlaysData?.neighborhoods, "neighborhoods");
    indexLayer(this.overlaysData?.super_neighborhoods, "super_neighborhoods");
    indexLayer(this.overlaysData?.historic_wards, "historic_wards");
    indexLayer(this.overlaysData?.historic_districts, "historic_districts");
    indexLayer(this.overlaysData?.heritage_districts, "heritage_districts");
    indexLayer(this.overlaysData?.nrhp_districts, "nrhp_districts");
    indexLayer(this.overlaysData?.potential_historic_districts, "potential_historic_districts");
    indexLayer(this.overlaysData?.cultural_districts, "cultural_districts");
    indexLayer(this.overlaysData?.holc_1937_zones, "holc_1937_zones");
    indexLayer(this.overlaysData?.annexations, "annexations");
    indexLayer(this.overlaysData?.historical_waterways, "historical_waterways", true);
    indexLayer(this.overlaysData?.historical_railroads, "historical_railroads", true);
  }

  _normalizeBoundaryOverlayKey(rawKey) {
    const k = String(rawKey || "").trim();
    const map = {
      lostHouston: "lost_houston",
      lost_houston: "lost_houston",
      "lost-houston-fill": "lost_houston",
      "lost-houston-line": "lost_houston",
      "lost-houston-extrusion": "lost_houston",
      plattedSubdivisions: "platted_subdivisions",
      platted_subdivisions: "platted_subdivisions",
      "platted-subdivisions-fill": "platted_subdivisions",
      landUseProtections: "land_use_protections",
      land_use_protections: "land_use_protections",
      "land-use-protections-fill": "land_use_protections",
      neighborhoods: "neighborhoods",
      "neighborhoods-fill": "neighborhoods",
      superNeighborhoods: "super_neighborhoods",
      super_neighborhoods: "super_neighborhoods",
      "super-neighborhoods-fill": "super_neighborhoods",
      historicWards: "historic_wards",
      historic_wards: "historic_wards",
      "historic-wards-fill": "historic_wards",
      historicDistricts: "historic_districts",
      historic_districts: "historic_districts",
      "historic-districts-fill": "historic_districts",
      heritageDistricts: "heritage_districts",
      heritage_districts: "heritage_districts",
      "heritage-districts-fill": "heritage_districts",
      nrhpDistricts: "nrhp_districts",
      nrhp_districts: "nrhp_districts",
      "nrhp-districts-fill": "nrhp_districts",
      potentialHistoricDistricts: "potential_historic_districts",
      potential_historic_districts: "potential_historic_districts",
      potential_districts: "potential_historic_districts",
      "potential-historic-districts-fill": "potential_historic_districts",
      culturalDistricts: "cultural_districts",
      cultural_districts: "cultural_districts",
      "cultural-districts-fill": "cultural_districts",
      holc1937: "holc_1937_zones",
      holc_1937: "holc_1937_zones",
      holc_zones: "holc_1937_zones",
      holc_1937_zones: "holc_1937_zones",
      "holc-1937-zones-fill": "holc_1937_zones",
      redBook1915: "red_book_1915",
      red_book: "red_book_1915",
      red_book_1915: "red_book_1915",
      "red-book-1915-circle": "red_book_1915",
      hahcTracker: "hahc_coa_filings",
      hahc_coa: "hahc_coa_filings",
      hahc: "hahc_coa_filings",
      coa: "hahc_coa_filings",
      hahc_coa_filings: "hahc_coa_filings",
      "hahc-coa-circle": "hahc_coa_filings",
      annexations: "annexations",
      "annexations-fill": "annexations",
      historicalWaterways: "historical_waterways",
      historical_waterways: "historical_waterways",
      "historical-waterways-line": "historical_waterways",
      "historical-waterways-line-solid": "historical_waterways",
      "historical-waterways-line-dashed": "historical_waterways",
      "historical-waterways-casing": "historical_waterways",
      historicalRailroads: "historical_railroads",
      historical_railroads: "historical_railroads",
      "historical-railroads-line": "historical_railroads",
      "historical-railroads-line-solid": "historical_railroads",
      "historical-railroads-line-dashed": "historical_railroads",
      "historical-railroads-casing": "historical_railroads",
      "historical-railroads-ties": "historical_railroads",
      "historical-railroads-depots": "historical_railroads",
    };
    return map[k] || k;
  }

  _minDistanceToGeometryDeg(lng, lat, geom) {
    if (!geom || !geom.coordinates) return Infinity;
    const cosLat = Math.cos((lat * Math.PI) / 180) || 0.868;
    const segDist = (x1, y1, x2, y2) => {
      const dx = (x2 - x1) * cosLat;
      const dy = y2 - y1;
      const lenSq = dx * dx + dy * dy;
      const px = (lng - x1) * cosLat;
      const py = lat - y1;
      if (lenSq <= 1e-16) return Math.hypot(px, py);
      const t = Math.max(0, Math.min(1, (px * dx + py * dy) / lenSq));
      return Math.hypot(px - t * dx, py - t * dy);
    };
    if (geom.type === "Point") {
      const [gx, gy] = geom.coordinates;
      return Math.hypot((lng - gx) * cosLat, lat - gy);
    }
    const lines =
      geom.type === "LineString"
        ? [geom.coordinates]
        : geom.type === "MultiLineString"
        ? geom.coordinates
        : [];
    let best = Infinity;
    for (const line of lines) {
      if (!Array.isArray(line)) continue;
      for (let i = 0; i < line.length - 1; i++) {
        const a = line[i];
        const b = line[i + 1];
        if (!a || !b) continue;
        const d = segDist(a[0], a[1], b[0], b[1]);
        if (d < best) best = d;
      }
    }
    return best;
  }

  _collectOverlappingFeaturesAtPoint(point, lngLat) {
    const state = this.filterStore.getState();
    const stack = [];
    const seenKeys = new Set();

    const makeBuildingTitle = (p) =>
      p.building_name ||
      p.landmark_name ||
      p.name ||
      p.address ||
      (p.hcad_num ? `HCAD ${p.hcad_num}` : "Historic Structure");

    const pushLinearOverlayHit = (lp, fallbackGeom, overlayKey, lid = "") => {
      const itemKey = `bnd:${overlayKey}:${lp.id || lp.name}`;
      if (seenKeys.has(itemKey)) return;
      seenKeys.add(itemKey);

      const isWaterway = overlayKey === "historical_waterways";
      let badge = lp.type_label || (isWaterway ? "Historical Waterway" : "Historical Railroad");
      let swatch = "#38bdf8";
      if (isWaterway) {
        const wt = lp.waterway_type;
        if (wt === "buried_gully") {
          badge = "Buried Gully";
          swatch = "#FBBF24";
        } else if (wt === "historic_oxbow") {
          badge = "Historic Oxbow";
          swatch = "#A78BFA";
        } else if (wt === "bayou") {
          badge = "Bayou";
          swatch = "#0EA5E9";
        } else {
          badge = "Historic Creek";
          swatch = "#22D3EE";
        }
      } else {
        const rt = lp.rail_type;
        if (rt === "depot") {
          badge = "Historic Depot";
          swatch = "#FDE047";
        } else if (rt === "abandoned_trail") {
          badge = "Abandoned Rail";
          swatch = "#FB7185";
        } else if (rt === "streetcar_interurban") {
          badge = "Streetcar Line";
          swatch = "#C084FC";
        } else if (rt === "industrial_spur") {
          badge = "Industrial Spur";
          swatch = "#94A3B8";
        } else {
          badge = "Pioneer Railroad";
          swatch = "#F59E0B";
        }
      }

      // Resolve full untruncated geometry from overlaysData so highlighting shows the entire line
      const fullFeat = (this.overlaysData?.[overlayKey]?.features || []).find(
        (of) =>
          of.properties &&
          ((lp.id && of.properties.id === lp.id) || (lp.name && of.properties.name === lp.name))
      );
      const fullProps = fullFeat?.properties ? { ...fullFeat.properties, ...lp } : lp;
      const fullGeom = fullFeat?.geometry || fallbackGeom || null;

      stack.push({
        key: itemKey,
        kind: "boundary",
        layerId: lid || overlayKey,
        overlayKey,
        layerKey: overlayKey,
        typeBadge: badge,
        badge,
        swatchColor: swatch,
        color: swatch,
        title: fullProps.name || badge,
        subtitle:
          fullProps.status ||
          fullProps.historic_company ||
          fullProps.era_notes ||
          badge,
        props: {
          ...fullProps,
          overlay_layer: overlayKey,
          is_boundary_feature: true,
        },
        geometry: fullGeom,
      });
    };

    // 1. Collect rendered point markers & building/parcel footprints at `point`
    if (!this.useCanvasFallback && this.map && point) {
      const BOUNDARY_FILL_LAYERS = new Set([
        "platted-subdivisions-fill",
        "land-use-protections-fill",
        "protection-armor-zones-fill",
        "neighborhoods-fill",
        "super-neighborhoods-fill",
        "historic-wards-fill",
        "holc-1937-zones-fill",
        "historic-districts-fill",
        "heritage-districts-fill",
        "nrhp-districts-fill",
        "potential-historic-districts-fill",
        "cultural-districts-fill",
        "annexations-fill",
      ]);
      const nonBoundaryLayers = [
        "portfolio-works-circle",
        "permit-watch-circle",
        "plat-watch-circle",
        "hahc-coa-circle",
        "red-book-1915-circle",
        "good-brick-circle",
        "relocated-structures-circle",
        "landmarks-circle",
        "nrhp-properties-circle",
        "thc-markers-circle",
        "curated-overrides-extrusion",
        "curated-overrides-fill",
        "historic-status-buildings-extrusion",
        "historic-status-buildings-fill",
        ...this.buildingExtrusionLayerIds,
        ...this.buildingFillLayerIds,
        "parcels-fill",
      ].filter((id) => this.map.getLayer(id));

      const rendered = nonBoundaryLayers.length
        ? this.map.queryRenderedFeatures(point, { layers: nonBoundaryLayers })
        : [];

      for (const f of rendered) {
        const lid = f.layer?.id || "";
        if (BOUNDARY_FILL_LAYERS.has(lid)) continue;
        const p = applyOverrideToProperties(f.properties || {}, this.curatedOverrides);

        if (lid === "portfolio-works-circle") {
          const pIdx = Number(p.portfolio_index);
          const pwKey = `pw:${p.id || pIdx}`;
          if (!seenKeys.has(pwKey)) {
            seenKeys.add(pwKey);
            const pwBadge = p.is_lost ? "† Portfolio (Lost)" : "Portfolio Work";
            const pwColor = p.is_lost ? "#EC4899" : "#F59E0B";
            stack.push({
              key: pwKey,
              kind: "marker",
              layerId: lid,
              overlayKey: "portfolio_work",
              layerKey: "portfolio_work",
              typeBadge: pwBadge,
              badge: pwBadge,
              swatchColor: pwColor,
              color: pwColor,
              title: p.name || p.address || "Portfolio Work",
              subtitle: [
                p.year_built ? `Built ${p.year_built}` : "",
                p.is_lost && p.year_demolished ? `Demolished ${p.year_demolished}` : "",
                p.address || "",
              ]
                .filter(Boolean)
                .join(" · "),
              props: {
                ...p,
                overlay_layer: "portfolio_work",
                is_boundary_feature: true,
              },
              geometry: f.geometry || null,
            });
          }
          continue;
        }

        if (lid === "hahc-coa-circle") {
          const fullFeat = this.getHahcFeatureById(p.id || p.hcad_num);
          const fullProps = fullFeat?.properties ? { ...fullFeat.properties } : { ...p };
          const hKey = `hahc:${fullProps.id || fullProps.hcad_num || stack.length}`;
          if (!seenKeys.has(hKey)) {
            seenKeys.add(hKey);
            const hBadge = fullProps.has_active_agenda
              ? "HAHC Active Agenda"
              : fullProps.has_demolition_relocation
              ? "HAHC Demo/Reloc"
              : fullProps.has_designation
              ? "HAHC Landmark Filing"
              : fullProps.has_denied_or_cor
              ? "HAHC Denied / COR"
              : "HAHC COA Precedent";
            const hColor = fullProps.has_active_agenda
              ? "#F43F5E"
              : fullProps.has_demolition_relocation
              ? "#EF4444"
              : fullProps.has_designation
              ? "#A855F7"
              : fullProps.has_denied_or_cor
              ? "#F59E0B"
              : "#14B8A6";
            stack.push({
              key: hKey,
              kind: "marker",
              layerId: lid,
              overlayKey: "hahc_coa_filings",
              layerKey: "hahc_coa_filings",
              typeBadge: hBadge,
              badge: hBadge,
              swatchColor: hColor,
              color: hColor,
              title: fullProps.address || fullProps.landmark_name || "HAHC COA Filing",
              subtitle: [
                fullProps.historic_district ? `${fullProps.historic_district} HD` : "",
                fullProps.latest_app_type || "",
                fullProps.latest_meeting_date || "",
              ]
                .filter(Boolean)
                .join(" · "),
              props: {
                ...fullProps,
                overlay_layer: "hahc_coa_filings",
                is_boundary_feature: true,
              },
              geometry: fullFeat?.geometry || f.geometry || null,
            });
          }
          continue;
        }

        if (lid === "red-book-1915-circle") {
          const fullFeat = this.getRedBookFeatureById(p.id);
          const fullProps = fullFeat?.properties ? { ...fullFeat.properties } : { ...p };
          const rbKey = `rb1915:${fullProps.id || fullProps.name || stack.length}`;
          if (!seenKeys.has(rbKey)) {
            seenKeys.add(rbKey);
            const cat = String(fullProps.category || "residence");
            const rbBadge =
              cat === "church"
                ? "1915 Church"
                : cat === "school"
                ? "1915 School"
                : cat === "business"
                ? "1915 Business"
                : "1915 Leader / Home";
            const rbColor =
              cat === "church"
                ? "#F59E0B"
                : cat === "school"
                ? "#38BDF8"
                : cat === "business"
                ? "#FB7185"
                : "#C084FC";
            stack.push({
              key: rbKey,
              kind: "marker",
              layerId: lid,
              overlayKey: "red_book_1915",
              layerKey: "red_book_1915",
              typeBadge: rbBadge,
              badge: rbBadge,
              swatchColor: rbColor,
              color: rbColor,
              title: fullProps.name || "1915 Red Book Site",
              subtitle: [
                fullProps.historic_address_1915 ? `1915: ${fullProps.historic_address_1915}` : "",
                fullProps.survival_label || "",
              ]
                .filter(Boolean)
                .join(" · "),
              props: {
                ...fullProps,
                overlay_layer: "red_book_1915",
                is_boundary_feature: true,
              },
              geometry: fullFeat?.geometry || f.geometry || null,
            });
          }
          continue;
        }

        if (
          (lid === "permit-watch-circle" ||
            lid === "plat-watch-circle" ||
            lid === "landmarks-circle" ||
            (lid === "nrhp-properties-circle" && !p.is_demolished_nrhp) ||
            lid === "good-brick-circle" ||
            lid === "relocated-structures-circle") &&
          (p.hcad_num || p.building_id)
        ) {
          const isBuildingPolyLayer = (bf) =>
            bf.layer?.id !== "portfolio-works-circle" &&
            bf.layer?.id !== "permit-watch-circle" &&
            bf.layer?.id !== "plat-watch-circle" &&
            bf.layer?.id !== "hahc-coa-circle" &&
            bf.layer?.id !== "red-book-1915-circle" &&
            bf.layer?.id !== "landmarks-circle" &&
            bf.layer?.id !== "nrhp-properties-circle" &&
            bf.layer?.id !== "good-brick-circle" &&
            bf.layer?.id !== "relocated-structures-circle" &&
            bf.layer?.id !== "thc-markers-circle";
          const bldHit =
            (p.building_id &&
              (this.overridesFC?.features || []).find(
                (bf) =>
                  bf.properties &&
                  (bf.properties.building_id === p.building_id || bf.properties.id === p.building_id)
              )) ||
            (p.hcad_num &&
              (this.overridesFC?.features || []).find(
                (bf) => bf.properties && bf.properties.hcad_num === p.hcad_num
              )) ||
            rendered.find(
              (bf) =>
                isBuildingPolyLayer(bf) &&
                bf.properties &&
                ((p.building_id &&
                  (bf.properties.building_id === p.building_id || bf.properties.id === p.building_id)) ||
                  (p.hcad_num && bf.properties.hcad_num === p.hcad_num))
            ) ||
            (p.building_id &&
              this.buildingsData.find(
                (bf) =>
                  bf.properties &&
                  (bf.properties.building_id === p.building_id || bf.properties.id === p.building_id)
              )) ||
            (p.hcad_num &&
              this.buildingsData.find(
                (bf) => bf.properties && bf.properties.hcad_num === p.hcad_num
              )) ||
            (lid !== "nrhp-properties-circle" ? rendered.find(isBuildingPolyLayer) : null);
          if (bldHit) {
            const baseBldProps = applyOverrideToProperties(
              bldHit.properties || {},
              this.curatedOverrides
            );
            const mergedProps = {
              ...baseBldProps,
              landmark_name:
                p.landmark_name ||
                baseBldProps.landmark_name ||
                p.resname ||
                p.name ||
                "",
              designation: p.designation || baseBldProps.designation || "",
              landmark_protection_tier:
                lid === "landmarks-circle"
                  ? p.protection_tier || baseBldProps.landmark_protection_tier || ""
                  : baseBldProps.landmark_protection_tier || "",
              landmark_pdf_url: p.pdf_url || baseBldProps.landmark_pdf_url || "",
              nrhp_refnum: p.refnum || baseBldProps.nrhp_refnum || "",
              nrhp_atlas_num: p.atlas_num || baseBldProps.nrhp_atlas_num || "",
              nrhp_resname: p.resname || baseBldProps.nrhp_resname || "",
              nrhp_thc_url: p.thc_atlas_url || baseBldProps.nrhp_thc_url || "",
              nrhp_pdf_url: p.nomination_pdf_url || baseBldProps.nrhp_pdf_url || "",
              nrhp_listed_date: p.listed_date || baseBldProps.nrhp_listed_date || "",
              good_brick_awards: p.good_brick_awards || baseBldProps.good_brick_awards || null,
              good_brick_summary: p.good_brick_summary || baseBldProps.good_brick_summary || "",
              good_brick_years: p.good_brick_years || baseBldProps.good_brick_years || "",
              builder: p.builder || baseBldProps.builder || "",
              builder_source: p.builder_source || baseBldProps.builder_source || "",
              original_permit: p.original_permit || baseBldProps.original_permit || "",
              original_cost: p.original_cost || baseBldProps.original_cost || "",
              was_relocated: Boolean(p.was_relocated || baseBldProps.was_relocated),
              relocation_note: p.relocation_note || baseBldProps.relocation_note || "",
              hist_contract: p.hist_contract || baseBldProps.hist_contract || null,
            };
            const bldKey = `bld:${
              mergedProps.building_id || mergedProps.id || mergedProps.hcad_num || makeBuildingTitle(mergedProps)
            }`;
            if (!seenKeys.has(bldKey)) {
              seenKeys.add(bldKey);
              if (mergedProps.hcad_num) seenKeys.add(`hcad:${mergedProps.hcad_num}`);
              const bTypeBadge =
                lid === "permit-watch-circle"
                  ? p.severity === "critical"
                    ? "⚠️ Demo Alert"
                    : "⚠️ Permit Watch"
                  : lid === "plat-watch-circle"
                  ? p.is_variance
                    ? "📋 Sec K Variance"
                    : p.is_public_hearing
                    ? "📋 Public Hearing"
                    : "📋 Plat Agenda"
                  : lid === "good-brick-circle"
                  ? "Good Brick"
                  : lid === "relocated-structures-circle"
                  ? mergedProps.was_relocated
                    ? "Relocated"
                    : "Contractor Portfolio"
                  : lid === "landmarks-circle"
                  ? p.protection_tier === "lm_waiver_vulnerable"
                    ? "90-Day Waiver LM"
                    : p.designation === "Protected Landmark" || p.protection_tier === "plm"
                    ? "Protected Landmark"
                    : "Landmark (in HD)"
                  : lid === "nrhp-properties-circle"
                  ? "NRHP Listed"
                  : "Building";
              const bSwatchColor =
                lid === "permit-watch-circle"
                  ? p.severity === "critical"
                    ? "#EF4444"
                    : "#F59E0B"
                  : lid === "plat-watch-circle"
                  ? p.severity === "critical"
                    ? "#F43F5E"
                    : "#8B5CF6"
                  : lid === "good-brick-circle"
                  ? "#95c959"
                  : lid === "relocated-structures-circle"
                  ? mergedProps.was_relocated
                    ? "#38bdf8"
                    : "#f59e0b"
                  : lid === "landmarks-circle"
                  ? p.protection_tier === "lm_waiver_vulnerable"
                    ? "#F59E0B"
                    : "#E63946"
                  : lid === "nrhp-properties-circle"
                  ? "#A855F7"
                  : "#fde047";
              stack.push({
                key: bldKey,
                kind: "building",
                layerId: lid,
                overlayKey: "",
                layerKey: "",
                typeBadge: bTypeBadge,
                badge: bTypeBadge,
                swatchColor: bSwatchColor,
                color: bSwatchColor,
                title: makeBuildingTitle(mergedProps),
                subtitle:
                  Number(mergedProps.year_built) >= 1836
                    ? `Built ${mergedProps.year_built}`
                    : mergedProps.address || "",
                props: mergedProps,
                geometry: bldHit.geometry || null,
              });
            }
            continue;
          }
        }

        if (lid === "nrhp-properties-circle") {
          const nKey = `nrhp:${p.refnum || p.id || p.resname || stack.length}`;
          if (!seenKeys.has(nKey)) {
            seenKeys.add(nKey);
            const isGone = Boolean(p.is_demolished_nrhp);
            const nBadge = isGone ? "† NRHP (Gone)" : "NRHP Site";
            const nColor = isGone ? "#94A3B8" : "#A855F7";
            stack.push({
              key: nKey,
              kind: "marker",
              layerId: lid,
              overlayKey: "nrhp_properties",
              layerKey: "nrhp_properties",
              typeBadge: nBadge,
              badge: nBadge,
              swatchColor: nColor,
              color: nColor,
              title: p.resname || p.name || `NRHP #${p.refnum || ""}`,
              subtitle: p.refnum
                ? `NRHP #${p.refnum}${p.listed_date ? ` (${String(p.listed_date).slice(0, 4)})` : ""}`
                : "National Register Property",
              props: {
                ...p,
                overlay_layer: "nrhp_properties",
                is_nrhp_marker: true,
              },
              geometry: f.geometry || null,
            });
          }
          continue;
        }

        if (lid === "thc-markers-circle") {
          const mKey = `thc:${p.marker_num || p.name || stack.length}`;
          if (!seenKeys.has(mKey)) {
            seenKeys.add(mKey);
            stack.push({
              key: mKey,
              kind: "marker",
              layerId: lid,
              overlayKey: "thc_markers",
              layerKey: "thc_markers",
              typeBadge: "THC Marker",
              badge: "THC Marker",
              swatchColor: "#c084fc",
              color: "#c084fc",
              title: p.name || p.landmark_name || `Marker #${p.marker_num || ""}`,
              subtitle: p.marker_num ? `THC Marker #${p.marker_num}` : "Historical Marker",
              props: p,
              geometry: f.geometry || null,
            });
          }
          continue;
        }

        const rawId = String(p.building_id || p.id || p.hcad_num || "").trim();
        const bKey = `bld:${rawId || makeBuildingTitle(p)}`;
        if (seenKeys.has(bKey) || (p.hcad_num && seenKeys.has(`hcad:${p.hcad_num}`))) {
          continue;
        }
        seenKeys.add(bKey);
        if (p.hcad_num) seenKeys.add(`hcad:${p.hcad_num}`);
        const bBadge =
          lid === "permit-watch-circle"
            ? p.severity === "critical"
              ? "⚠️ Demo Alert"
              : "⚠️ Permit Watch"
            : lid === "plat-watch-circle"
            ? p.is_variance
              ? "📋 Sec K Variance"
              : p.is_public_hearing
              ? "📋 Public Hearing"
              : "📋 Plat Agenda"
            : lid === "parcels-fill"
            ? "Tax Parcel"
            : "Building";
        const bColor =
          lid === "permit-watch-circle"
            ? p.severity === "critical"
              ? "#EF4444"
              : "#F59E0B"
            : lid === "plat-watch-circle"
            ? p.severity === "critical"
              ? "#F43F5E"
              : "#8B5CF6"
            : lid === "parcels-fill"
            ? "#94a3b8"
            : "#fde047";
        stack.push({
          key: bKey,
          kind: "building",
          layerId: lid,
          overlayKey: "",
          layerKey: "",
          typeBadge: bBadge,
          badge: bBadge,
          swatchColor: bColor,
          color: bColor,
          title: makeBuildingTitle(p),
          subtitle:
            Number(p.year_built) >= 1836 ? `Built ${p.year_built}` : p.address || "",
          props: p,
          geometry: f.geometry || null,
        });
      }

      // 1b. Collect rendered historical waterways & railroads within an 8px hit box
      const linearOverlayLayers = [
        state.layers?.historicalRailroads ? "historical-railroads-depots" : null,
        state.layers?.historicalRailroads ? "historical-railroads-line-solid" : null,
        state.layers?.historicalRailroads ? "historical-railroads-line-dashed" : null,
        state.layers?.historicalRailroads ? "historical-railroads-casing" : null,
        state.layers?.historicalWaterways ? "historical-waterways-line-solid" : null,
        state.layers?.historicalWaterways ? "historical-waterways-line-dashed" : null,
        state.layers?.historicalWaterways ? "historical-waterways-casing" : null,
      ].filter((id) => id && this.map.getLayer(id));

      if (linearOverlayLayers.length) {
        const hitBox = [
          [point.x - 8, point.y - 8],
          [point.x + 8, point.y + 8],
        ];
        const linearHits = this.map.queryRenderedFeatures(hitBox, {
          layers: linearOverlayLayers,
        });
        for (const lf of linearHits) {
          const lp = lf.properties || {};
          const lid = lf.layer?.id || "";
          const isWaterway =
            lid.startsWith("historical-waterways") || lp.overlay_layer === "historical_waterways";
          const overlayKey = isWaterway ? "historical_waterways" : "historical_railroads";
          pushLinearOverlayHit(lp, lf.geometry, overlayKey, lid);
        }
      }
    } else if (this.useCanvasFallback && this.canvasState && point) {
      const boxes = this.canvasState.renderedBBoxes || [];
      for (let i = boxes.length - 1; i >= 0; i--) {
        const b = boxes[i];
        if (!b || b.props?.isTourStop) continue;
        if (point.x >= b.minX && point.x <= b.maxX && point.y >= b.minY && point.y <= b.maxY) {
          const p = applyOverrideToProperties(b.props || {}, this.curatedOverrides);
          if (p.overlay_layer === "portfolio_work") {
            const pwKey = `pw:${p.id || p.portfolio_index}`;
            if (!seenKeys.has(pwKey)) {
              seenKeys.add(pwKey);
              const pwBadge = p.is_lost ? "† Portfolio (Lost)" : "Portfolio Work";
              const pwColor = p.is_lost ? "#EC4899" : "#F59E0B";
              stack.push({
                key: pwKey,
                kind: "marker",
                layerId: "portfolio-works-circle",
                overlayKey: "portfolio_work",
                layerKey: "portfolio_work",
                typeBadge: pwBadge,
                badge: pwBadge,
                swatchColor: pwColor,
                color: pwColor,
                title: p.name || p.address || "Portfolio Work",
                subtitle: p.address || "",
                props: { ...p, overlay_layer: "portfolio_work", is_boundary_feature: true },
                geometry: b.geom || null,
              });
            }
            continue;
          }
          if (p.overlay_layer === "hahc_coa_filings") {
            const hKey = `hahc:${p.id || p.hcad_num || stack.length}`;
            if (!seenKeys.has(hKey)) {
              seenKeys.add(hKey);
              const hBadge = p.has_active_agenda ? "HAHC Active Agenda" : "HAHC COA Precedent";
              const hColor = p.has_active_agenda ? "#F43F5E" : "#14B8A6";
              stack.push({
                key: hKey,
                kind: "marker",
                layerId: "hahc-coa-circle",
                overlayKey: "hahc_coa_filings",
                layerKey: "hahc_coa_filings",
                typeBadge: hBadge,
                badge: hBadge,
                swatchColor: hColor,
                color: hColor,
                title: p.address || p.landmark_name || "HAHC COA Filing",
                subtitle: [p.historic_district ? `${p.historic_district} HD` : "", p.latest_app_type || ""]
                  .filter(Boolean)
                  .join(" · "),
                props: { ...p, overlay_layer: "hahc_coa_filings", is_boundary_feature: true },
                geometry: b.geom || null,
              });
            }
            continue;
          }
          if (p.overlay_layer === "red_book_1915") {
            const rbKey = `rb1915:${p.id || p.name || stack.length}`;
            if (!seenKeys.has(rbKey)) {
              seenKeys.add(rbKey);
              const cat = String(p.category || "residence");
              const rbBadge =
                cat === "church"
                  ? "1915 Church"
                  : cat === "school"
                  ? "1915 School"
                  : cat === "business"
                  ? "1915 Business"
                  : "1915 Leader / Home";
              const rbColor =
                cat === "church"
                  ? "#F59E0B"
                  : cat === "school"
                  ? "#38BDF8"
                  : cat === "business"
                  ? "#FB7185"
                  : "#C084FC";
              stack.push({
                key: rbKey,
                kind: "marker",
                layerId: "red-book-1915-circle",
                overlayKey: "red_book_1915",
                layerKey: "red_book_1915",
                typeBadge: rbBadge,
                badge: rbBadge,
                swatchColor: rbColor,
                color: rbColor,
                title: p.name || "1915 Red Book Site",
                subtitle: [
                  p.historic_address_1915 ? `1915: ${p.historic_address_1915}` : "",
                  p.survival_label || "",
                ]
                  .filter(Boolean)
                  .join(" · "),
                props: { ...p, overlay_layer: "red_book_1915", is_boundary_feature: true },
                geometry: b.geom || null,
              });
            }
            continue;
          }
          const rawId = String(p.building_id || p.id || p.hcad_num || p.marker_num || "").trim();
          const bKey = `bld:${rawId || makeBuildingTitle(p)}`;
          if (seenKeys.has(bKey) || (p.hcad_num && seenKeys.has(`hcad:${p.hcad_num}`))) {
            continue;
          }
          seenKeys.add(bKey);
          if (p.hcad_num) seenKeys.add(`hcad:${p.hcad_num}`);
          const cBadge = p.marker_num ? "THC Marker" : "Building";
          const cColor = p.marker_num ? "#c084fc" : "#fde047";
          stack.push({
            key: bKey,
            kind: p.marker_num ? "marker" : "building",
            layerId: "canvas-feature",
            overlayKey: "",
            layerKey: "",
            typeBadge: cBadge,
            badge: cBadge,
            swatchColor: cColor,
            color: cColor,
            title: makeBuildingTitle(p),
            subtitle:
              Number(p.year_built) >= 1836 ? `Built ${p.year_built}` : p.address || "",
            props: p,
            geometry: b.geom || null,
          });
        }
      }
    }

    // 1c. Direct geometric polyline/depot hit-test fallback (works in both WebGL and 2D Canvas)
    const lng = Number(lngLat?.lng ?? lngLat?.[0]);
    const lat = Number(lngLat?.lat ?? lngLat?.[1]);
    if (
      Number.isFinite(lng) &&
      Number.isFinite(lat) &&
      (state.layers?.historicalWaterways || state.layers?.historicalRailroads)
    ) {
      const curZoom = this.useCanvasFallback
        ? Number(this.canvasState?.zoom || 13)
        : Number(this.map?.getZoom?.() || 13);
      // ~9 pixels tolerance in degrees at current zoom
      const degPerPixel = 360 / (256 * Math.pow(2, curZoom));
      const hitTolDeg = Math.max(0.00008, Math.min(0.0035, degPerPixel * 9.5));

      const linearCandidates = [];
      if (state.layers?.historicalRailroads && this.overlaysData?.historical_railroads?.features) {
        for (const rf of this.overlaysData.historical_railroads.features) {
          const d = this._minDistanceToGeometryDeg(lng, lat, rf.geometry);
          const tol = rf.properties?.rail_type === "depot" ? hitTolDeg * 1.35 : hitTolDeg;
          if (d <= tol) {
            linearCandidates.push({
              dist: rf.properties?.rail_type === "depot" ? d * 0.5 : d,
              feat: rf,
              overlayKey: "historical_railroads",
            });
          }
        }
      }
      if (state.layers?.historicalWaterways && this.overlaysData?.historical_waterways?.features) {
        for (const wf of this.overlaysData.historical_waterways.features) {
          const d = this._minDistanceToGeometryDeg(lng, lat, wf.geometry);
          if (d <= hitTolDeg) {
            linearCandidates.push({
              dist: d,
              feat: wf,
              overlayKey: "historical_waterways",
            });
          }
        }
      }
      linearCandidates.sort((a, b) => a.dist - b.dist);
      for (const cand of linearCandidates) {
        pushLinearOverlayHit(
          cand.feat.properties || {},
          cand.feat.geometry,
          cand.overlayKey,
          cand.overlayKey
        );
      }
    }

    // 2. Collect all visible boundary polygons containing `lngLat` from boundarySpatialIndex
    if (Number.isFinite(lng) && Number.isFinite(lat)) {
      if (!Array.isArray(this.boundarySpatialIndex) || !this.boundarySpatialIndex.length) {
        this._buildBoundarySpatialIndex();
      }
      const activeWardEra = resolveActiveWardEra(state);
      const showAnnex = Boolean(state.layers?.annexations);
      const activeAnnexDecade = resolveActiveAnnexationDecade(state);
      const showSpokes = state.showAnnexationSpokes !== false;

      const LAYER_META = {
        lost_houston: {
          enabled:
            Boolean(state.layers?.lostHouston) || Boolean(state.historicalSnapshotMode),
          priority: 2,
          typeBadge: "† Lost Houston",
          swatchColor: "#F472B6",
        },
        platted_subdivisions: {
          enabled: Boolean(state.layers?.plattedSubdivisions),
          priority: 10,
          typeBadge: "Platted Subdiv",
          swatchColor: "#2dd4bf",
        },
        land_use_protections: {
          enabled: Boolean(state.layers?.landUseProtections),
          priority: 15,
          typeBadge: "Ch.42 Protection",
          swatchColor: "#f59e0b",
        },
        historic_districts: {
          enabled: Boolean(state.layers?.historicDistricts),
          priority: 20,
          typeBadge: "Historic Dist",
          swatchColor: "#a855f7",
        },
        potential_historic_districts: {
          enabled: Boolean(state.layers?.potentialHistoricDistricts),
          priority: 22,
          typeBadge: "Potential HD",
          swatchColor: "#FB923C",
        },
        heritage_districts: {
          enabled: Boolean(state.layers?.heritageDistricts),
          priority: 25,
          typeBadge: "Heritage Dist",
          swatchColor: "#ec4899",
        },
        nrhp_districts: {
          enabled: Boolean(state.layers?.nrhpDistricts),
          priority: 30,
          typeBadge: "NRHP Dist",
          swatchColor: "#10b981",
        },
        cultural_districts: {
          enabled: Boolean(state.layers?.culturalDistricts),
          priority: 35,
          typeBadge: "Cultural Dist",
          swatchColor: "#F472B6",
        },
        neighborhoods: {
          enabled: Boolean(state.layers?.neighborhoods),
          priority: 40,
          typeBadge: "Neighborhood",
          swatchColor: "#38bdf8",
        },
        holc_1937_zones: {
          enabled: Boolean(state.layers?.holc1937),
          priority: 46,
          typeBadge: "1937 HOLC",
          swatchColor: "#EF4444",
        },
        super_neighborhoods: {
          enabled: Boolean(state.layers?.superNeighborhoods),
          priority: 50,
          typeBadge: "Super Nbhd",
          swatchColor: "#818cf8",
        },
        historic_wards: {
          enabled: Boolean(state.layers?.historicWards),
          priority: 60,
          typeBadge: `${activeWardEra === 1903 ? "1903–05" : activeWardEra} Ward`,
          swatchColor: "#fb923c",
        },
        annexations: {
          enabled: showAnnex,
          priority: 70,
          typeBadge: "Annexation",
          swatchColor: "#f59e0b",
        },
      };

      const matchingBoundaries = [];
      for (const entry of this.boundarySpatialIndex) {
        const meta = LAYER_META[entry.overlayKey];
        if (!meta || !meta.enabled) continue;
        if (entry.overlayKey === "lost_houston") {
          const yb = Number(entry.props.year_built || 1836);
          const yd = Number(entry.props.year_demolished || 9999);
          const minY = Number(state.minYear ?? 1836);
          const maxY = Number(state.maxYear ?? 2026);
          if (state.historicalSnapshotMode) {
            if (yb > maxY || yd <= maxY) continue;
          } else {
            if (yb > maxY || yd < minY) continue;
          }
        } else if (entry.overlayKey === "historic_wards") {
          const entryEra = Number(
            entry.props.era || entry.props.ward_era || entry.props.era_year || 0
          );
          if (entryEra !== activeWardEra) continue;
        } else if (entry.overlayKey === "annexations") {
          const annexDecade = Number(entry.props.decade || entry.props.year || 1836);
          if (annexDecade !== activeAnnexDecade) continue;
          if (!showSpokes && entry.props.annex_subtype === "spoke_or_spa") continue;
        } else if (entry.overlayKey === "holc_1937_zones") {
          const activeHolc = String(state.holcGradeFilter || "all").trim().toUpperCase();
          if (activeHolc !== "ALL" && String(entry.props.holc_grade || "").toUpperCase() !== activeHolc) {
            continue;
          }
        }
        const [minLng, minLat, maxLng, maxLat] = entry.bbox;
        if (lng < minLng || lng > maxLng || lat < minLat || lat > maxLat) continue;
        if (!this._pointInPolygonGeometry(lng, lat, entry.feature.geometry)) continue;
        let dynBadge = meta.typeBadge;
        let dynSwatch = entry.props.color || meta.swatchColor;
        if (entry.overlayKey === "lost_houston") {
          const yb = entry.props.year_built || "?";
          const yd = entry.props.year_demolished || "?";
          dynBadge = `† Lost (${yb}-${yd})`;
          dynSwatch = getSanbornMaterialColors(entry.props.sanborn_material).color;
        } else if (entry.overlayKey === "holc_1937_zones") {
          dynBadge = `1937 HOLC ${entry.props.zone_code || ""} (${entry.props.grade_short || entry.props.holc_grade || ""})`;
          dynSwatch = entry.props.color || "#EF4444";
        } else if (entry.overlayKey === "platted_subdivisions" && entry.props.has_deed_docs) {
          dynBadge = "📜 Platted Subdiv";
          dynSwatch = "#FBBF24";
        } else if (entry.overlayKey === "land_use_protections") {
          const pt = entry.props.protection_type;
          if (pt === "smbl") {
            dynBadge = "Ch.42 Min Setback";
            dynSwatch = "#34D399";
          } else if (pt === "conservation") {
            dynBadge = "Conservation Dist";
            dynSwatch = "#FB7185";
          } else {
            dynBadge = "Ch.42 Min Lot Size";
            dynSwatch = "#FBBF24";
          }
        } else if (entry.overlayKey === "potential_historic_districts") {
          if (entry.props.already_designated_coh_hd) {
            dynBadge = "2019 Survey (Now HD)";
            dynSwatch = "#A855F7";
          } else if (entry.props.survey_subtype === "eligible_site") {
            dynBadge = "Eligible Site";
            dynSwatch = "#F59E0B";
          } else {
            dynBadge = "Potential HD";
            dynSwatch = "#FB923C";
          }
        }
        matchingBoundaries.push({
          entry,
          priority: meta.priority,
          typeBadge: dynBadge,
          swatchColor: dynSwatch,
        });
      }

      matchingBoundaries.sort((a, b) => {
        if (a.priority !== b.priority) return a.priority - b.priority;
        return a.entry.areaDeg2 - b.entry.areaDeg2;
      });

      const superNbhdNamesLower = new Set(
        matchingBoundaries
          .filter((mb) => mb.entry.overlayKey === "super_neighborhoods")
          .map((mb) => String(mb.entry.props.name || "").trim().toLowerCase())
          .filter(Boolean)
      );

      const lostHoustonFrontItems = [];
      for (const mb of matchingBoundaries) {
        const ep = mb.entry.props;
        const title = ep.name || ep.era_label || ep.historic_district || "Boundary";
        const titleLower = String(title || "").trim().toLowerCase();
        // Skip duplicate Pitney Bowes macro-neighborhood polygons when a COH Super Neighborhood with the same name is present
        if (
          mb.entry.overlayKey === "neighborhoods" &&
          superNbhdNamesLower.has(titleLower)
        ) {
          continue;
        }
        const bndKey = `bnd:${mb.entry.overlayKey}:${ep.id || ep.name || ep.era_label}`;
        if (seenKeys.has(bndKey)) continue;
        seenKeys.add(bndKey);
        const stackItem = {
          key: bndKey,
          kind: "boundary",
          layerId: `${mb.entry.overlayKey}-fill`,
          overlayKey: mb.entry.overlayKey,
          layerKey: mb.entry.overlayKey,
          typeBadge: mb.typeBadge,
          badge: mb.typeBadge,
          swatchColor: mb.swatchColor,
          color: mb.swatchColor,
          title,
          subtitle:
            mb.entry.overlayKey === "lost_houston"
              ? `${ep.category || "Demolished Landmark"} (${ep.year_built || "?"}-${ep.year_demolished || "?"})`
              : Number(ep.building_count) > 0
              ? `${Number(ep.building_count).toLocaleString()} structures`
              : ep.type || mb.typeBadge,
          props: {
            ...ep,
            overlay_layer: mb.entry.overlayKey,
            is_boundary_feature: true,
          },
          geometry: mb.entry.feature.geometry,
        };
        if (mb.entry.overlayKey === "lost_houston") {
          lostHoustonFrontItems.push(stackItem);
        } else {
          stack.push(stackItem);
        }
      }
      if (lostHoustonFrontItems.length) {
        stack.unshift(...lostHoustonFrontItems);
      }
    }

    return stack;
  }

  _handleMapPointClick(point, lngLat) {
    const stack = this._collectOverlappingFeaturesAtPoint(point, lngLat);
    if (!stack.length) return;

    let nextIndex = 0;
    if (
      point &&
      this._lastOverlapClickPoint &&
      Array.isArray(this.overlapStack) &&
      this.overlapStack.length > 1 &&
      Math.hypot(
        point.x - this._lastOverlapClickPoint.x,
        point.y - this._lastOverlapClickPoint.y
      ) <= 14 &&
      this.overlapStack[0]?.key === stack[0]?.key
    ) {
      nextIndex = (this.overlapStackIndex + 1) % stack.length;
    }

    this._lastOverlapClickPoint = point ? { x: point.x, y: point.y } : null;
    this.overlapStack = stack;
    this.selectOverlapStackItem(nextIndex);
  }

  selectOverlapStackItem(index) {
    if (!Array.isArray(this.overlapStack) || !this.overlapStack.length) return false;
    const safeIdx =
      ((Number(index) % this.overlapStack.length) + this.overlapStack.length) %
      this.overlapStack.length;
    this.overlapStackIndex = safeIdx;
    const item = this.overlapStack[safeIdx];
    if (!item) return false;

    if (item.kind === "boundary") {
      this.clearSelection({ keepOverlapStack: true, keepBoundary: true });
      const highlighted = this.highlightBoundaryByIdOrName({
        id: item.props.id || "",
        name: item.props.name || item.props.era_label || item.title || "",
        layerKey: item.overlayKey,
        eraYear: item.props.era || item.props.ward_era || item.props.era_year || null,
        flyTo: true,
        inspect: true,
        keepOverlapStack: true,
      });
      if (!highlighted && item.geometry) {
        const feat = {
          type: "Feature",
          geometry: item.geometry,
          properties: {
            ...item.props,
            overlay_layer: item.overlayKey,
            is_boundary_feature: true,
          },
        };
        this.selectedBoundaryFeature = feat;
        if (this.useCanvasFallback) {
          this._renderCanvas2D();
        } else if (this.map) {
          const selBndSrc = this.map.getSource("selected-boundary-src");
          if (selBndSrc) {
            selBndSrc.setData({ type: "FeatureCollection", features: [feat] });
          }
        }
        const geomMeta = this._computeGeometryBBoxAndCentroid(item.geometry);
        if (geomMeta) {
          const curZoom = this.useCanvasFallback
            ? Number(this.canvasState?.zoom || 13)
            : Number(this.map?.getZoom?.() || 13);
          if (
            geomMeta.bbox &&
            !this.useCanvasFallback &&
            this.map &&
            typeof this.map.fitBounds === "function" &&
            (Math.abs(geomMeta.bbox[2] - geomMeta.bbox[0]) > 1e-5 ||
              Math.abs(geomMeta.bbox[3] - geomMeta.bbox[1]) > 1e-5)
          ) {
            try {
              this.map.fitBounds(
                [
                  [geomMeta.bbox[0], geomMeta.bbox[1]],
                  [geomMeta.bbox[2], geomMeta.bbox[3]],
                ],
                {
                  padding: { top: 80, bottom: 110, left: 360, right: 380 },
                  maxZoom: item.overlayKey === "lost_houston" ? 17.5 : Math.max(curZoom, 15.8),
                  duration: 850,
                }
              );
            } catch (_e) {
              this.flyToLocation({
                lng: geomMeta.lng,
                lat: geomMeta.lat,
                zoom: Math.max(curZoom, 16.0),
              });
            }
          } else if (Number.isFinite(geomMeta.lng) && Number.isFinite(geomMeta.lat)) {
            this.flyToLocation({
              lng: geomMeta.lng,
              lat: geomMeta.lat,
              zoom: Math.max(curZoom, 16.5),
            });
          }
        }
        if (this.onSelectFeature) {
          this.onSelectFeature(feat.properties);
        }
      }
    } else if (item.overlayKey === "red_book_1915") {
      this.clearSelection({ keepOverlapStack: true, keepBoundary: true });
      const fullFeat = this.getRedBookFeatureById(item.props?.id);
      const geom = fullFeat?.geometry || item.geometry || null;
      const fullProps = {
        ...(fullFeat?.properties || item.props || {}),
        overlay_layer: "red_book_1915",
        is_boundary_feature: true,
      };
      if (geom) {
        const feat = {
          type: "Feature",
          geometry: geom,
          properties: fullProps,
        };
        this.selectedBoundaryFeature = feat;
        if (this.useCanvasFallback) {
          this._renderCanvas2D();
        } else if (this.map) {
          const selBndSrc = this.map.getSource("selected-boundary-src");
          if (selBndSrc) {
            selBndSrc.setData({ type: "FeatureCollection", features: [feat] });
          }
        }
        const coords = Array.isArray(geom.coordinates) ? geom.coordinates : null;
        if (coords && Number.isFinite(coords[0]) && Number.isFinite(coords[1])) {
          const curZoom = this.useCanvasFallback
            ? Number(this.canvasState?.zoom || 13)
            : Number(this.map?.getZoom?.() || 13);
          fullProps.lng = coords[0];
          fullProps.lat = coords[1];
          this.flyToLocation({
            lng: coords[0],
            lat: coords[1],
            zoom: Math.max(curZoom, 17.4),
          });
        }
      }
      if (this.onSelectFeature) {
        this.onSelectFeature(fullProps);
      }
    } else if (item.overlayKey === "portfolio_work") {
      const pIdx = Number(item.props?.portfolio_index);
      if (Number.isFinite(pIdx) && typeof this.onSelectPortfolioWork === "function") {
        this.onSelectPortfolioWork(pIdx);
      }
    } else if (item.overlayKey === "hahc_coa_filings") {
      this.clearSelection({ keepOverlapStack: true, keepBoundary: true });
      const fullFeat = this.getHahcFeatureById(item.props?.id || item.props?.hcad_num);
      const geom = fullFeat?.geometry || item.geometry || null;
      const fullProps = {
        ...(fullFeat?.properties || item.props || {}),
        overlay_layer: "hahc_coa_filings",
        is_boundary_feature: true,
      };
      if (geom) {
        const feat = {
          type: "Feature",
          geometry: geom,
          properties: fullProps,
        };
        this.selectedBoundaryFeature = feat;
        if (this.useCanvasFallback) {
          this._renderCanvas2D();
        } else if (this.map) {
          const selBndSrc = this.map.getSource("selected-boundary-src");
          if (selBndSrc) {
            selBndSrc.setData({ type: "FeatureCollection", features: [feat] });
          }
        }
        const coords = Array.isArray(geom.coordinates) ? geom.coordinates : null;
        if (coords && Number.isFinite(coords[0]) && Number.isFinite(coords[1])) {
          const curZoom = this.useCanvasFallback
            ? Number(this.canvasState?.zoom || 13)
            : Number(this.map?.getZoom?.() || 13);
          fullProps.lng = coords[0];
          fullProps.lat = coords[1];
          this.flyToLocation({
            lng: coords[0],
            lat: coords[1],
            zoom: Math.max(curZoom, 17.4),
          });
        }
      }
      if (this.onSelectFeature) {
        this.onSelectFeature(fullProps);
      }
    } else {
      this.clearHighlightedBoundary();
      this.highlightAndInspectFeature(item.props, item.geometry || null, {
        keepOverlapStack: true,
      });
      // When clicking any Map Layers & Overlays point marker (Good Brick, COH Landmarks,
      // THC Markers, Relocated Structures, Active Permit Watch) or clicking a building while
      // zoomed out, smoothly fly and zoom to the structure.
      const curZoom = this.useCanvasFallback
        ? Number(this.canvasState?.zoom || 13)
        : Number(this.map?.getZoom?.() || 13);
      const lid = String(item.layerId || "");
      const isOverlayMarkerOrLine =
        item.kind === "marker" ||
        Boolean(item.overlayKey) ||
        lid.includes("good-brick") ||
        lid.includes("landmarks") ||
        lid.includes("nrhp-properties") ||
        lid.includes("thc-markers") ||
        lid.includes("relocated") ||
        lid.includes("permit-watch") ||
        lid.includes("plat-watch") ||
        lid.includes("lost-houston") ||
        lid.includes("historical-waterways") ||
        lid.includes("historical-railroads");
      if (isOverlayMarkerOrLine || curZoom < 16.5) {
        let targetLng = Number(
          this.selectedFeatureProps?.lng ??
            this.selectedFeatureProps?.lon ??
            item.props?.lng ??
            item.props?.lon
        );
        let targetLat = Number(this.selectedFeatureProps?.lat ?? item.props?.lat);
        const geomMeta = this._computeGeometryBBoxAndCentroid(
          this.selectedFeatureGeometry || item.geometry
        );
        if ((!Number.isFinite(targetLng) || !Number.isFinite(targetLat)) && geomMeta) {
          targetLng = geomMeta.lng;
          targetLat = geomMeta.lat;
        }
        if (Number.isFinite(targetLng) && Number.isFinite(targetLat)) {
          const isLinearOverlay =
            lid.includes("historical-waterways") ||
            lid.includes("historical-railroads") ||
            item.overlayKey === "historical_waterways" ||
            item.overlayKey === "historical_railroads";
          const targetZoom = isLinearOverlay
            ? Math.max(curZoom, 15.2)
            : Math.max(curZoom, 17.4);
          this.flyToLocation({
            lng: targetLng,
            lat: targetLat,
            zoom: targetZoom,
          });
        }
      }
    }

    if (typeof this.onOverlapStackChange === "function") {
      this.onOverlapStackChange(this.overlapStack, this.overlapStackIndex);
    }
    return true;
  }

  cycleOverlapStack(step = 1) {
    if (!Array.isArray(this.overlapStack) || this.overlapStack.length < 2) return false;
    return this.selectOverlapStackItem(this.overlapStackIndex + step);
  }

  _pointInRing(lng, lat, ring) {
    if (!Array.isArray(ring) || ring.length < 3) return false;
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const xi = Number(ring[i][0]);
      const yi = Number(ring[i][1]);
      const xj = Number(ring[j][0]);
      const yj = Number(ring[j][1]);
      const intersect =
        yi > lat !== yj > lat &&
        lng < ((xj - xi) * (lat - yi)) / (yj - yi || 1e-12) + xi;
      if (intersect) inside = !inside;
    }
    return inside;
  }

  _pointInPolygonGeometry(lng, lat, geom) {
    if (!geom || !geom.coordinates) return false;
    const checkPoly = (rings) => {
      if (!Array.isArray(rings) || !rings.length) return false;
      if (!this._pointInRing(lng, lat, rings[0])) return false;
      for (let k = 1; k < rings.length; k++) {
        if (this._pointInRing(lng, lat, rings[k])) return false;
      }
      return true;
    };
    if (geom.type === "Polygon") {
      return checkPoly(geom.coordinates);
    }
    if (geom.type === "MultiPolygon") {
      for (const polyRings of geom.coordinates) {
        if (checkPoly(polyRings)) return true;
      }
    }
    return false;
  }

  resolveGeographicContextAtPoint(lng, lat) {
    if (!Number.isFinite(lng) || !Number.isFinite(lat)) return null;
    if (!Array.isArray(this.boundarySpatialIndex) || !this.boundarySpatialIndex.length) {
      this._buildBoundarySpatialIndex();
    }
    const nhMatches = [];
    const platMatches = [];
    const protMatches = [];
    const hdMatches = [];
    const heritageMatches = [];
    const nrhpDistMatches = [];
    const potentialHdMatches = [];
    const culturalDistMatches = [];
    const holcMatches = [];
    let snMatch = null;
    let wardMatch1903 = null;

    for (const entry of this.boundarySpatialIndex) {
      const [minLng, minLat, maxLng, maxLat] = entry.bbox;
      if (lng < minLng || lng > maxLng || lat < minLat || lat > maxLat) continue;
      if (!this._pointInPolygonGeometry(lng, lat, entry.feature.geometry)) continue;
      if (entry.overlayKey === "neighborhoods") {
        nhMatches.push(entry);
      } else if (entry.overlayKey === "platted_subdivisions") {
        platMatches.push(entry);
      } else if (entry.overlayKey === "land_use_protections") {
        protMatches.push(entry);
      } else if (entry.overlayKey === "historic_districts") {
        hdMatches.push(entry);
      } else if (entry.overlayKey === "heritage_districts") {
        heritageMatches.push(entry);
      } else if (entry.overlayKey === "nrhp_districts") {
        nrhpDistMatches.push(entry);
      } else if (entry.overlayKey === "potential_historic_districts") {
        potentialHdMatches.push(entry);
      } else if (entry.overlayKey === "cultural_districts") {
        culturalDistMatches.push(entry);
      } else if (entry.overlayKey === "holc_1937_zones") {
        holcMatches.push(entry);
      } else if (entry.overlayKey === "super_neighborhoods" && !snMatch) {
        snMatch = entry;
      } else if (
        entry.overlayKey === "historic_wards" &&
        Number(entry.props.era || entry.props.ward_era || entry.props.era_year) === 1903 &&
        !wardMatch1903
      ) {
        wardMatch1903 = entry;
      }
    }

    nhMatches.sort((a, b) => a.areaDeg2 - b.areaDeg2);
    platMatches.sort((a, b) => a.areaDeg2 - b.areaDeg2);
    protMatches.sort((a, b) => a.areaDeg2 - b.areaDeg2);
    hdMatches.sort((a, b) => a.areaDeg2 - b.areaDeg2);
    heritageMatches.sort((a, b) => a.areaDeg2 - b.areaDeg2);
    nrhpDistMatches.sort((a, b) => a.areaDeg2 - b.areaDeg2);
    potentialHdMatches.sort((a, b) => a.areaDeg2 - b.areaDeg2);
    culturalDistMatches.sort((a, b) => a.areaDeg2 - b.areaDeg2);
    holcMatches.sort((a, b) => a.areaDeg2 - b.areaDeg2);
    const primaryNh = nhMatches[0]?.props?.name || "";
    const primaryPlat = platMatches[0]?.props?.name || "";
    const aliasSet = new Set();
    const aliasList = [];
    const addAlias = (val) => {
      const s = String(val || "").trim();
      if (!s) return;
      const k = s.toLowerCase();
      if (primaryNh && k === primaryNh.toLowerCase()) return;
      if (aliasSet.has(k)) return;
      aliasSet.add(k);
      aliasList.push(s);
    };
    for (const m of nhMatches) {
      if (m.props.name && m.props.name !== primaryNh) {
        addAlias(m.props.name);
      }
      const rawAlt = m.props.aliases || m.props.alt_names;
      if (Array.isArray(rawAlt)) {
        for (const a of rawAlt) addAlias(a);
      } else if (typeof rawAlt === "string" && rawAlt.trim()) {
        for (const a of rawAlt.split(/\s*\|\s*|\s*;\s*/)) addAlias(a);
      }
    }

    return {
      neighborhood: primaryNh,
      neighborhood_aliases: aliasList,
      platted_subdivision: primaryPlat,
      platted_subdivision_id: platMatches[0]?.props?.id || "",
      platted_subdivision_props: platMatches[0]?.props || null,
      land_use_protections: protMatches.map((m) => m.props),
      historic_district: hdMatches[0]?.props?.name || "",
      historic_district_props: hdMatches[0]?.props || null,
      heritage_district: heritageMatches[0]?.props?.name || "",
      heritage_district_props: heritageMatches[0]?.props || null,
      nrhp_districts: nrhpDistMatches.map((m) => m.props),
      potential_historic_districts: potentialHdMatches.map((m) => m.props),
      cultural_districts: culturalDistMatches.map((m) => m.props),
      holc_1937_zone: holcMatches[0]?.props || null,
      super_neighborhood:
        snMatch?.props?.name ||
        nhMatches[0]?.props?.super_neighborhood ||
        platMatches[0]?.props?.super_neighborhood ||
        "",
      historic_ward:
        wardMatch1903?.props?.name ||
        nhMatches[0]?.props?.historic_ward ||
        platMatches[0]?.props?.historic_ward ||
        "",
      neighborhood_id: nhMatches[0]?.props?.id || "",
      super_neighborhood_id: snMatch?.props?.id || "",
      historic_ward_id: wardMatch1903?.props?.id || "",
    };
  }

  findLostHoustonAtSite({ hcadNum = "", lng = null, lat = null } = {}) {
    const features = this.overlaysData?.lost_houston?.features;
    if (!Array.isArray(features) || !features.length) return [];
    const cleanHcad = String(hcadNum || "").trim();
    const numLng = Number(lng);
    const numLat = Number(lat);
    const hasCoords = Number.isFinite(numLng) && Number.isFinite(numLat);
    const exactMatches = [];
    const nearMatches = [];
    const seenIds = new Set();

    for (const feat of features) {
      const p = feat?.properties || {};
      const id = String(p.id || p.name || "");
      if (!id || seenIds.has(id)) continue;

      let isExact = false;
      if (cleanHcad && Array.isArray(p.site_hcad_nums)) {
        if (p.site_hcad_nums.some((h) => String(h || "").trim() === cleanHcad)) {
          isExact = true;
        }
      }
      if (!isExact && hasCoords && feat.geometry) {
        if (this._pointInPolygonGeometry(numLng, numLat, feat.geometry)) {
          isExact = true;
        }
      }
      if (isExact) {
        seenIds.add(id);
        exactMatches.push(p);
        continue;
      }
      if (!cleanHcad && hasCoords) {
        const cLng = Number(p.label_lng ?? p.lng ?? p.lon);
        const cLat = Number(p.label_lat ?? p.lat);
        if (
          Number.isFinite(cLng) &&
          Number.isFinite(cLat) &&
          Math.hypot(numLng - cLng, numLat - cLat) <= 0.00018
        ) {
          seenIds.add(id);
          nearMatches.push(p);
        }
      }
    }

    const matches = exactMatches.length > 0 ? exactMatches : nearMatches;
    matches.sort((a, b) => Number(a.year_built || 9999) - Number(b.year_built || 9999));
    return matches;
  }

  highlightBoundaryByIdOrName(arg1 = {}, arg2 = {}) {
    let opts = {};
    if (typeof arg1 === "string") {
      opts = {
        ...arg2,
        id: arg1,
        name: arg1,
      };
    } else if (arg1 && typeof arg1 === "object") {
      opts = {
        ...arg2,
        ...arg1,
      };
    }
    const {
      id = "",
      name = "",
      layerKey = "",
      eraYear = null,
      flyTo = opts.fitBounds !== undefined ? Boolean(opts.fitBounds) : true,
      inspect = opts.openInspector !== undefined ? Boolean(opts.openInspector) : false,
      keepOverlapStack = false,
    } = opts;

    if (!keepOverlapStack) {
      this.overlapStack = [];
      this.overlapStackIndex = 0;
      this._lastOverlapClickPoint = null;
      if (typeof this.onOverlapStackChange === "function") {
        this.onOverlapStackChange([], 0);
      }
    }

    if (!Array.isArray(this.boundarySpatialIndex) || !this.boundarySpatialIndex.length) {
      this._buildBoundarySpatialIndex();
    }
    const targetId = String(id || "").trim();
    const targetName = String(name || "").trim().toLowerCase();
    const targetLayer = this._normalizeBoundaryOverlayKey(layerKey);

    let matchEntry = null;
    if (targetId) {
      matchEntry = this.boundarySpatialIndex.find(
        (e) =>
          String(e.props.id || "") === targetId &&
          (!targetLayer || e.overlayKey === targetLayer)
      );
    }
    if (!matchEntry && targetName) {
      // Exact primary name match first
      matchEntry = this.boundarySpatialIndex.find((e) => {
        if (targetLayer && e.overlayKey !== targetLayer) return false;
        const entryEra = Number(e.props.era || e.props.ward_era || e.props.era_year || 0);
        if (
          e.overlayKey === "historic_wards" &&
          eraYear &&
          entryEra !== Number(eraYear)
        ) {
          return false;
        }
        if (
          e.overlayKey === "historic_wards" &&
          !eraYear &&
          entryEra !== resolveActiveWardEra(this.filterStore.getState())
        ) {
          return false;
        }
        return (
          String(e.props.name || e.props.era_label || "")
            .trim()
            .toLowerCase() === targetName
        );
      });
    }
    if (!matchEntry && targetName) {
      // Fallback: match any historic ward era or alias or full_name
      matchEntry = this.boundarySpatialIndex.find((e) => {
        if (targetLayer && e.overlayKey !== targetLayer) return false;
        if (
          String(e.props.name || e.props.era_label || "")
            .trim()
            .toLowerCase() === targetName
        ) {
          return true;
        }
        if (
          String(e.props.full_name || "")
            .trim()
            .toLowerCase() === targetName
        ) {
          return true;
        }
        const rawAlt = e.props.aliases || e.props.alt_names;
        const alts = Array.isArray(rawAlt)
          ? rawAlt
          : typeof rawAlt === "string"
          ? rawAlt.split(/\s*\|\s*|\s*;\s*/)
          : [];
        return alts.some((a) => String(a || "").trim().toLowerCase() === targetName);
      });
    }

    if (!matchEntry) return false;

    const feat = {
      type: "Feature",
      geometry: matchEntry.feature.geometry,
      properties: {
        ...matchEntry.props,
        overlay_layer: matchEntry.overlayKey,
        is_boundary_feature: true,
      },
    };
    if (inspect) {
      this.clearSelection({ keepOverlapStack: true, keepBoundary: true });
    }
    this.selectedBoundaryFeature = feat;

    if (this.useCanvasFallback) {
      this._renderCanvas2D();
    } else if (this.map) {
      const selBndSrc = this.map.getSource("selected-boundary-src");
      if (selBndSrc) {
        selBndSrc.setData({
          type: "FeatureCollection",
          features: [feat],
        });
      }
    }

    if (flyTo) {
      const [minLng, minLat, maxLng, maxLat] = matchEntry.bbox;
      const isPointFeature =
        matchEntry.feature?.geometry?.type === "Point" ||
        (Math.abs(maxLng - minLng) < 1e-6 && Math.abs(maxLat - minLat) < 1e-6);
      const isLostHouston = matchEntry.overlayKey === "lost_houston";
      const isSmallBoundary =
        isLostHouston ||
        matchEntry.overlayKey === "platted_subdivisions" ||
        matchEntry.overlayKey === "neighborhoods" ||
        matchEntry.overlayKey === "historic_districts" ||
        matchEntry.overlayKey === "heritage_districts" ||
        matchEntry.overlayKey === "potential_historic_districts";
      if (isPointFeature) {
        this.flyToLocation({
          lng: matchEntry.centroid[0],
          lat: matchEntry.centroid[1],
          zoom: 15.5,
        });
      } else if (!this.useCanvasFallback && this.map && typeof this.map.fitBounds === "function") {
        try {
          this.map.fitBounds(
            [
              [minLng, minLat],
              [maxLng, maxLat],
            ],
            {
              padding: { top: 80, bottom: 110, left: 360, right: 380 },
              maxZoom: isLostHouston
                ? 17.4
                : matchEntry.overlayKey === "platted_subdivisions"
                ? 16.2
                : isSmallBoundary
                ? 15.6
                : 14.2,
              duration: 950,
            }
          );
        } catch (_e) {
          this.flyToLocation({
            lng: matchEntry.centroid[0],
            lat: matchEntry.centroid[1],
            zoom: isLostHouston ? 17.0 : isSmallBoundary ? 15.0 : 13.5,
          });
        }
      } else {
        this.flyToLocation({
          lng: matchEntry.centroid[0],
          lat: matchEntry.centroid[1],
          zoom: isLostHouston ? 17.0 : isSmallBoundary ? 15.0 : 13.5,
        });
      }
    }

    if (inspect && this.onSelectFeature) {
      this.onSelectFeature({
        ...feat.properties,
        lng: matchEntry.centroid[0],
        lat: matchEntry.centroid[1],
      });
    }
    return true;
  }

  inspectRedBookById(rbId, { flyTo = true, zoom = 17.5 } = {}) {
    const fullFeat = this.getRedBookFeatureById(rbId);
    if (!fullFeat) return false;
    this.clearSelection({ keepOverlapStack: false, keepBoundary: true });
    const coords = Array.isArray(fullFeat.geometry?.coordinates)
      ? fullFeat.geometry.coordinates
      : null;
    const fullProps = {
      ...(fullFeat.properties || {}),
      overlay_layer: "red_book_1915",
      is_boundary_feature: true,
    };
    if (coords && Number.isFinite(coords[0]) && Number.isFinite(coords[1])) {
      fullProps.lng = coords[0];
      fullProps.lat = coords[1];
    }
    const feat = {
      type: "Feature",
      geometry: fullFeat.geometry,
      properties: fullProps,
    };
    this.selectedBoundaryFeature = feat;
    if (this.useCanvasFallback) {
      this._renderCanvas2D();
    } else if (this.map) {
      const selBndSrc = this.map.getSource("selected-boundary-src");
      if (selBndSrc) {
        selBndSrc.setData({ type: "FeatureCollection", features: [feat] });
      }
    }
    if (flyTo && Number.isFinite(fullProps.lng) && Number.isFinite(fullProps.lat)) {
      const curZoom = this.useCanvasFallback
        ? Number(this.canvasState?.zoom || 13)
        : Number(this.map?.getZoom?.() || 13);
      this.flyToLocation({
        lng: fullProps.lng,
        lat: fullProps.lat,
        zoom: Math.max(curZoom, zoom),
      });
    }
    if (this.onSelectFeature) {
      this.onSelectFeature(fullProps);
    }
    return true;
  }

  inspectHahcById(hahcId, { flyTo = true, zoom = 17.5 } = {}) {
    const fullFeat = this.getHahcFeatureById(hahcId);
    if (!fullFeat) return false;
    this.clearSelection({ keepOverlapStack: false, keepBoundary: true });
    const coords = Array.isArray(fullFeat.geometry?.coordinates)
      ? fullFeat.geometry.coordinates
      : null;
    const fullProps = {
      ...(fullFeat.properties || {}),
      overlay_layer: "hahc_coa_filings",
      is_boundary_feature: true,
    };
    if (coords && Number.isFinite(coords[0]) && Number.isFinite(coords[1])) {
      fullProps.lng = coords[0];
      fullProps.lat = coords[1];
    }
    const feat = {
      type: "Feature",
      geometry: fullFeat.geometry,
      properties: fullProps,
    };
    this.selectedBoundaryFeature = feat;
    if (this.useCanvasFallback) {
      this._renderCanvas2D();
    } else if (this.map) {
      const selBndSrc = this.map.getSource("selected-boundary-src");
      if (selBndSrc) {
        selBndSrc.setData({ type: "FeatureCollection", features: [feat] });
      }
    }
    if (flyTo && Number.isFinite(fullProps.lng) && Number.isFinite(fullProps.lat)) {
      const curZoom = this.useCanvasFallback
        ? Number(this.canvasState?.zoom || 13)
        : Number(this.map?.getZoom?.() || 13);
      this.flyToLocation({
        lng: fullProps.lng,
        lat: fullProps.lat,
        zoom: Math.max(curZoom, zoom),
      });
    }
    if (this.onSelectFeature) {
      this.onSelectFeature(fullProps);
    }
    return true;
  }

  inspectNrhpById(nrhpId, { flyTo = true, zoom = 17.5 } = {}) {
    const clean = String(nrhpId || "").trim().toLowerCase();
    if (!clean) return false;
    const feats = this.overlaysData?.nrhp_properties?.features || [];
    const fullFeat = feats.find((f) => {
      const p = f?.properties || {};
      return (
        String(p.id || "").trim().toLowerCase() === clean ||
        String(p.refnum || "").trim().toLowerCase() === clean ||
        `nrhp_${String(p.refnum || "").trim().toLowerCase()}` === clean
      );
    });
    if (!fullFeat) return false;
    this.clearSelection({ keepOverlapStack: false, keepBoundary: true });
    const coords = Array.isArray(fullFeat.geometry?.coordinates)
      ? fullFeat.geometry.coordinates
      : null;
    const fullProps = {
      ...(fullFeat.properties || {}),
      overlay_layer: "nrhp_properties",
      is_nrhp_marker: true,
      is_boundary_feature: true,
    };
    if (coords && Number.isFinite(coords[0]) && Number.isFinite(coords[1])) {
      fullProps.lng = coords[0];
      fullProps.lat = coords[1];
    }
    const feat = {
      type: "Feature",
      geometry: fullFeat.geometry,
      properties: fullProps,
    };
    this.selectedBoundaryFeature = feat;
    if (this.useCanvasFallback) {
      this._renderCanvas2D();
    } else if (this.map) {
      const selBndSrc = this.map.getSource("selected-boundary-src");
      if (selBndSrc) {
        selBndSrc.setData({ type: "FeatureCollection", features: [feat] });
      }
    }
    if (flyTo && Number.isFinite(fullProps.lng) && Number.isFinite(fullProps.lat)) {
      const curZoom = this.useCanvasFallback
        ? Number(this.canvasState?.zoom || 13)
        : Number(this.map?.getZoom?.() || 13);
      this.flyToLocation({
        lng: fullProps.lng,
        lat: fullProps.lat,
        zoom: Math.max(curZoom, zoom),
      });
    }
    if (this.onSelectFeature) {
      this.onSelectFeature(fullProps);
    }
    return true;
  }

  clearHighlightedBoundary() {
    this.selectedBoundaryFeature = null;
    if (this.useCanvasFallback) {
      this._renderCanvas2D();
    } else if (this.map) {
      const selBndSrc = this.map.getSource("selected-boundary-src");
      if (selBndSrc) {
        selBndSrc.setData({ type: "FeatureCollection", features: [] });
      }
    }
  }

  highlightAndInspectFeature(props, clickedGeometry = null, opts = {}) {
    if (!props) return;
    if (!opts?.keepOverlapStack) {
      this.overlapStack = [];
      this.overlapStackIndex = 0;
      this._lastOverlapClickPoint = null;
      if (typeof this.onOverlapStackChange === "function") {
        this.onOverlapStackChange([], 0);
      }
    }
    const mergedProps = applyOverrideToProperties(props, this.curatedOverrides);
    this.selectedFeatureId = mergedProps.id || "";
    this.selectedFeatureProps = mergedProps;

    // Resolve the single building polygon geometry so clicking one building on a
    // multi-building parcel (e.g. Rice University) never highlights all buildings on that parcel.
    const singleGeom = this._resolveSelectedPolygonGeometry(mergedProps, clickedGeometry);
    this.selectedFeatureGeometry = singleGeom;

    // Dynamically enrich any countywide PMTiles shard building or parcel with
    // Vernacular Neighborhood, Platted Subdivision, Historical Aliases, Super Neighborhood,
    // 1920 Historic Ward, Plat Citation, and Chapter 42 Land-Use Protections
    {
      let queryLng = Number(mergedProps.lng ?? mergedProps.lon);
      let queryLat = Number(mergedProps.lat);
      if (!Number.isFinite(queryLng) || !Number.isFinite(queryLat)) {
        const geomMeta = this._computeGeometryBBoxAndCentroid(singleGeom || clickedGeometry);
        if (geomMeta) {
          queryLng = geomMeta.lng;
          queryLat = geomMeta.lat;
        }
      }
      if (Number.isFinite(queryLng) && Number.isFinite(queryLat)) {
        const geoCtx = this.resolveGeographicContextAtPoint(queryLng, queryLat);
        if (geoCtx) {
          if (!mergedProps.neighborhood && geoCtx.neighborhood) {
            mergedProps.neighborhood = geoCtx.neighborhood;
          }
          if (!mergedProps.subdivision && geoCtx.platted_subdivision) {
            mergedProps.subdivision = geoCtx.platted_subdivision;
          }
          if (
            (!mergedProps.neighborhood_aliases || !mergedProps.neighborhood_aliases.length) &&
            geoCtx.neighborhood_aliases?.length
          ) {
            mergedProps.neighborhood_aliases = geoCtx.neighborhood_aliases;
          }
          if (!mergedProps.super_neighborhood && geoCtx.super_neighborhood) {
            mergedProps.super_neighborhood = geoCtx.super_neighborhood;
          }
          if (!mergedProps.historic_ward && geoCtx.historic_ward) {
            mergedProps.historic_ward = geoCtx.historic_ward;
          }
          if (geoCtx.platted_subdivision_props) {
            mergedProps._platted_subdivision_props = geoCtx.platted_subdivision_props;
          }
          if (Array.isArray(geoCtx.land_use_protections) && geoCtx.land_use_protections.length) {
            mergedProps._land_use_protections = geoCtx.land_use_protections;
          }
          if (
            (!mergedProps.historic_district ||
              String(mergedProps.historic_district).trim().toLowerCase() === "none") &&
            geoCtx.historic_district
          ) {
            mergedProps.historic_district = geoCtx.historic_district;
          }
          mergedProps._geo_context = geoCtx;
        }
      }
      const bldIdForLookup = mergedProps.building_id || mergedProps.id || "";
      const lmRecord = this.getLandmarkForBuilding({
        hcadNum: mergedProps.hcad_num,
        buildingId: bldIdForLookup,
      });
      if (lmRecord) {
        mergedProps._landmark_record = lmRecord;
        if (!mergedProps.landmark_name && lmRecord.name) {
          mergedProps.landmark_name = lmRecord.name;
        }
        if (!mergedProps.landmark_type && lmRecord.designation) {
          mergedProps.landmark_type = lmRecord.designation;
        }
        if (!mergedProps.landmark_pdf_url && lmRecord.pdf_url) {
          mergedProps.landmark_pdf_url = lmRecord.pdf_url;
        }
      }
      const nrhpRecord = this.getNrhpPropertyForBuilding({
        hcadNum: mergedProps.hcad_num,
        buildingId: bldIdForLookup,
      });
      if (nrhpRecord) {
        mergedProps._nrhp_property_record = nrhpRecord;
        if (!mergedProps.nrhp_refnum && nrhpRecord.refnum) {
          mergedProps.nrhp_refnum = nrhpRecord.refnum;
        }
        if (!mergedProps.nrhp_resname && nrhpRecord.resname) {
          mergedProps.nrhp_resname = nrhpRecord.resname;
        }
        if (!mergedProps.nrhp_thc_url && nrhpRecord.thc_atlas_url) {
          mergedProps.nrhp_thc_url = nrhpRecord.thc_atlas_url;
        }
        if (!mergedProps.nrhp_pdf_url && nrhpRecord.nomination_pdf_url) {
          mergedProps.nrhp_pdf_url = nrhpRecord.nomination_pdf_url;
        }
      }
      const redBookEntries = this.getRedBookEntriesForBuilding({
        hcadNum: mergedProps.hcad_num,
        buildingId: bldIdForLookup,
      });
      if (redBookEntries.length) {
        mergedProps._red_book_entries = redBookEntries;
      }
    }

    if (this.useCanvasFallback) {
      this._renderCanvas2D();
    } else if (this.map) {
      const curState = this.filterStore.getState();
      const showBuildings = curState.layers?.buildings !== false;
      const passesFilter = featureMatchesFilter(mergedProps, curState);
      const selOutlineVis = showBuildings && passesFilter ? "visible" : "none";
      if (this.map.getLayer("selected-feature-outline")) {
        this.map.setLayoutProperty("selected-feature-outline", "visibility", selOutlineVis);
      }
      if (this.map.getLayer("selected-feature-3d-extrusion")) {
        this.map.setLayoutProperty(
          "selected-feature-3d-extrusion",
          "visibility",
          showBuildings && curState.extrude3D && passesFilter ? "visible" : "none"
        );
      }
      const selSrc = this.map.getSource("selected-feature-src");
      if (selSrc && singleGeom) {
        selSrc.setData({
          type: "FeatureCollection",
          features: [{ type: "Feature", geometry: singleGeom, properties: mergedProps }],
        });
        for (const hlId of this.highlightLayerIds) {
          if (this.map.getLayer(hlId)) {
            this.map.setFilter(hlId, ["==", ["get", "id"], ""]);
          }
        }
      } else {
        if (selSrc) {
          selSrc.setData({ type: "FeatureCollection", features: [] });
        }
        for (const hlId of this.highlightLayerIds) {
          if (this.map.getLayer(hlId)) {
            this.map.setLayoutProperty(hlId, "visibility", selOutlineVis);
            this.map.setFilter(hlId, ["==", ["get", "id"], this.selectedFeatureId]);
          }
        }
      }
      this._updateSelectedFeature3DSource(curState);
    }
    if (this.onSelectFeature) {
      this.onSelectFeature(mergedProps);
    }
  }

  clearSelection(opts = {}) {
    this._selectNavSeq = (this._selectNavSeq || 0) + 1;
    this.selectedFeatureId = null;
    this.selectedFeatureProps = null;
    this.selectedFeatureGeometry = null;
    if (!opts?.keepBoundary) {
      this.clearHighlightedBoundary();
    }
    if (!opts?.keepOverlapStack) {
      this.overlapStack = [];
      this.overlapStackIndex = 0;
      this._lastOverlapClickPoint = null;
      if (typeof this.onOverlapStackChange === "function") {
        this.onOverlapStackChange([], 0);
      }
    }
    if (this.useCanvasFallback) {
      this._renderCanvas2D();
    } else if (this.map) {
      const selSrc = this.map.getSource("selected-feature-src");
      if (selSrc) {
        selSrc.setData({ type: "FeatureCollection", features: [] });
      }
      const sel3DSrc = this.map.getSource("selected-feature-3d-src");
      if (sel3DSrc) {
        sel3DSrc.setData({ type: "FeatureCollection", features: [] });
      }
      for (const hlId of this.highlightLayerIds) {
        if (this.map.getLayer(hlId)) {
          this.map.setFilter(hlId, ["==", ["get", "id"], ""]);
        }
      }
    }
  }

  _getCanonicalBuildingKey(props, fallbackCoord = "") {
    if (!props) return fallbackCoord;
    const rawId = String(props.building_id || props.id || "").trim();
    const hcad = String(props.hcad_num || "").trim();
    if (rawId.includes("#")) {
      if (/#aux_\d+$/.test(rawId) && hcad) return hcad;
      return rawId;
    }
    if (hcad && !hcad.startsWith("bld_")) {
      return hcad;
    }
    return rawId || fallbackCoord;
  }

  _approxPolygonAreaDeg2(geom) {
    if (!geom || !geom.coordinates) return 0;
    const polys =
      geom.type === "Polygon"
        ? [geom.coordinates]
        : geom.type === "MultiPolygon" && Array.isArray(geom.coordinates)
        ? geom.coordinates
        : [];
    let total = 0;
    for (const poly of polys) {
      const ring = poly && poly[0];
      if (!Array.isArray(ring) || ring.length < 3) continue;
      let sum = 0;
      for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
        const xi = Number(ring[i][0]) || 0;
        const yi = Number(ring[i][1]) || 0;
        const xj = Number(ring[j][0]) || 0;
        const yj = Number(ring[j][1]) || 0;
        sum += xj * yi - xi * yj;
      }
      total += Math.abs(sum) * 0.5;
    }
    return total;
  }

  computeViewportHistogram() {
    if (!this.onViewportStats) return;

    const bounds = this.useCanvasFallback
      ? this._getCanvasBounds()
      : this.map
      ? this.map.getBounds()
      : null;
    if (!bounds) return;

    const west = bounds.getWest();
    const south = bounds.getSouth();
    const east = bounds.getEast();
    const north = bounds.getNorth();
    const state = this.filterStore.getState();

    const decadeCounts = {};
    for (let d = 1830; d <= 2020; d += 10) {
      decadeCounts[String(d)] = 0;
    }

    let inViewportTotal = 0;
    let matchingFilterCount = 0;
    let oldestInView = 9999;
    let oldestAddress = "";
    let contributingCount = 0;
    let landmarkCount = 0;

    const iso = this.isolatedBoundary;
    const isoGeom = iso?.feature?.geometry || null;
    const isoBBox = iso?.bbox || null;

    const getSamplePt = (geom) => {
      if (!geom || !geom.coordinates) return null;
      const ring =
        geom.type === "Polygon"
          ? geom.coordinates[0]
          : geom.type === "MultiPolygon" && geom.coordinates[0]
          ? geom.coordinates[0][0]
          : null;
      if (!ring || !ring.length) return null;
      let sx = 0;
      let sy = 0;
      let n = 0;
      const step = Math.max(1, Math.floor(ring.length / 6));
      for (let i = 0; i < ring.length; i += step) {
        const pt = ring[i];
        if (Array.isArray(pt) && Number.isFinite(pt[0]) && Number.isFinite(pt[1])) {
          sx += pt[0];
          sy += pt[1];
          n += 1;
        }
      }
      return n > 0 ? [sx / n, sy / n] : null;
    };

    const isPolygonInIsolated = (geom) => {
      if (!isoGeom) return true;
      const pt = getSamplePt(geom);
      if (!pt) return false;
      const [lon, lat] = pt;
      if (isoBBox && (lon < isoBBox[0] || lon > isoBBox[2] || lat < isoBBox[1] || lat > isoBBox[3])) {
        return false;
      }
      return this._pointInPolygonGeometry(lon, lat, isoGeom);
    };

    // In WebGL mode with countywide PMTiles shards, query rendered vector tile features first
    let usedRenderedFeatures = false;
    if (!this.useCanvasFallback && this.map) {
      const queryLayers = [
        "curated-overrides-fill",
        "curated-overrides-extrusion",
        "historic-status-buildings-fill",
        "historic-status-buildings-extrusion",
        ...this.buildingFillLayerIds,
        ...this.buildingExtrusionLayerIds,
      ].filter((id) => this.map.getLayer(id));
      if (queryLayers.length > 0) {
        const rendered = this.map.queryRenderedFeatures({ layers: queryLayers });
        if (rendered.length > 0) {
          usedRenderedFeatures = true;
          const seenIds = new Set();
          for (const feat of rendered) {
            if (isoGeom && !isPolygonInIsolated(feat.geometry)) continue;
            const p = applyOverrideToProperties(feat.properties || {}, this.curatedOverrides);
            if (p.suppress_only) continue;
            const samplePt = getSamplePt(feat.geometry);
            const coordSig = samplePt
              ? `${Number(samplePt[0]).toFixed(5)},${Number(samplePt[1]).toFixed(5)}`
              : "";
            const key = this._getCanonicalBuildingKey(p, coordSig);
            if (key) {
              if (seenIds.has(key)) continue;
              seenIds.add(key);
            }
            inViewportTotal += 1;
            const yr = Number(p.year_built) || 0;
            const dec = Number(p.decade) || 0;
            if (yr >= 1836 && dec >= 1830 && dec <= 2020) {
              decadeCounts[String(dec)] = (decadeCounts[String(dec)] || 0) + 1;
            }
            if (featureMatchesFilter(p, state)) {
              matchingFilterCount += 1;
              if (yr >= 1836 && yr < oldestInView) {
                oldestInView = yr;
                oldestAddress = p.landmark_name || p.address || "Historic Structure";
              }
              if (p.contributing === "Contributing") {
                contributingCount += 1;
              }
              if (p.landmark_name || p.landmark_type) {
                landmarkCount += 1;
              }
            }
          }
        }
      }
    }

    if (!usedRenderedFeatures) {
      const seenFallbackIds = new Set();
      const allFallbackFeatures = [
        ...(this.overridesFC?.features || []),
        ...this.buildingsData,
      ];
      for (const feat of allFallbackFeatures) {
        const geom = feat.geometry;
        const pt = getSamplePt(geom);
        if (!pt) continue;

        const [lon, lat] = pt;
        if (!isoGeom && (lon < west || lon > east || lat < south || lat > north)) continue;
        if (isoGeom && !isPolygonInIsolated(geom)) continue;

        const p = feat.properties || {};
        if (p.suppress_only) continue;
        const coordSig = `${lon.toFixed(5)},${lat.toFixed(5)}`;
        const key = this._getCanonicalBuildingKey(p, coordSig);
        if (key) {
          if (seenFallbackIds.has(key)) continue;
          seenFallbackIds.add(key);
        }

        inViewportTotal += 1;
        const yr = Number(p.year_built) || 0;
        const dec = Number(p.decade) || 0;

        if (yr >= 1836 && dec >= 1830 && dec <= 2020) {
          decadeCounts[String(dec)] = (decadeCounts[String(dec)] || 0) + 1;
        }

        if (featureMatchesFilter(p, state)) {
          matchingFilterCount += 1;
          if (yr >= 1836 && yr < oldestInView) {
            oldestInView = yr;
            oldestAddress = p.landmark_name || p.address || "Historic Structure";
          }
          if (p.contributing === "Contributing") {
            contributingCount += 1;
          }
          if (p.landmark_name || p.landmark_type) {
            landmarkCount += 1;
          }
        }
      }
    }

    const vp = this.useCanvasFallback
      ? {
          lat: this.canvasState.lat,
          lng: this.canvasState.lng,
          zoom: this.canvasState.zoom,
          pitch: this.canvasState.pitch,
        }
      : {
          lat: this.map.getCenter().lat,
          lng: this.map.getCenter().lng,
          zoom: this.map.getZoom(),
          pitch: this.map.getPitch(),
        };

    this.onViewportStats({
      inViewportTotal,
      matchingFilterCount,
      oldestYear: oldestInView < 9999 ? oldestInView : null,
      oldestAddress,
      contributingCount,
      landmarkCount,
      decadeCounts,
      viewport: vp,
    });
  }

  _findBoundaryEntry(spec = {}) {
    if (!Array.isArray(this.boundarySpatialIndex) || !this.boundarySpatialIndex.length) {
      this._buildBoundarySpatialIndex();
    }
    const targetId = String(spec.id || "").trim();
    const targetName = String(spec.name || spec.id || "").trim().toLowerCase();
    const stripDistrictSuffix = (s) =>
      String(s || "")
        .trim()
        .toLowerCase()
        .replace(/\s+(protected\s+)?(historic|heritage|nrhp|cultural)\s+district(\s*\(potential\))?$/i, "")
        .replace(/\s+super\s+neighborhood$/i, "")
        .trim();
    const strippedTargetName = stripDistrictSuffix(targetName);
    const targetLayer = this._normalizeBoundaryOverlayKey(spec.layerKey || spec.overlay_layer || "");
    const eraYear = spec.eraYear || spec.era || null;

    let matchEntry = null;
    if (targetId) {
      matchEntry = this.boundarySpatialIndex.find(
        (e) =>
          String(e.props.id || "") === targetId &&
          (!targetLayer || e.overlayKey === targetLayer)
      );
    }
    if (!matchEntry && targetName) {
      matchEntry = this.boundarySpatialIndex.find((e) => {
        if (targetLayer && e.overlayKey !== targetLayer) return false;
        const entryEra = Number(e.props.era || e.props.ward_era || e.props.era_year || 0);
        if (e.overlayKey === "historic_wards" && eraYear && entryEra !== Number(eraYear)) {
          return false;
        }
        const entryName = String(e.props.name || e.props.era_label || "")
          .trim()
          .toLowerCase();
        return entryName === targetName || stripDistrictSuffix(entryName) === strippedTargetName;
      });
    }
    if (!matchEntry && targetName) {
      matchEntry = this.boundarySpatialIndex.find((e) => {
        if (targetLayer && e.overlayKey !== targetLayer) return false;
        const entryName = String(e.props.name || e.props.era_label || "")
          .trim()
          .toLowerCase();
        if (
          entryName === targetName ||
          stripDistrictSuffix(entryName) === strippedTargetName ||
          (strippedTargetName.length >= 4 && entryName.includes(strippedTargetName))
        ) {
          return true;
        }
        if (
          String(e.props.full_name || "")
            .trim()
            .toLowerCase() === targetName
        ) {
          return true;
        }
        const rawAlt = e.props.aliases || e.props.alt_names;
        const alts = Array.isArray(rawAlt)
          ? rawAlt
          : typeof rawAlt === "string"
          ? rawAlt.split(/\s*\|\s*|\s*;\s*/)
          : [];
        return alts.some((a) => String(a || "").trim().toLowerCase() === targetName);
      });
    }
    if (!matchEntry && targetName && targetLayer) {
      // Fallback across all boundary layers if layerKey didn't match
      matchEntry = this.boundarySpatialIndex.find((e) => {
        const entryName = String(e.props.name || e.props.era_label || "")
          .trim()
          .toLowerCase();
        return (
          entryName === targetName ||
          stripDistrictSuffix(entryName) === strippedTargetName ||
          String(e.props.id || "") === targetId
        );
      });
    }
    return matchEntry || null;
  }

  _resolveIsolatedBoundaryFromSpec(spec, opts = {}) {
    if (!spec) {
      this.isolatedBoundary = null;
      return null;
    }
    const matchEntry = this._findBoundaryEntry(spec);
    if (!matchEntry) {
      this.isolatedBoundary = null;
      return null;
    }
    const validMode =
      spec.mode === "footprints_only" || spec.mode === "border_only"
        ? spec.mode
        : "contents";
    const feat = {
      type: "Feature",
      geometry: matchEntry.feature.geometry,
      properties: {
        ...matchEntry.props,
        overlay_layer: matchEntry.overlayKey,
        is_boundary_feature: true,
      },
    };
    this.isolatedBoundary = {
      feature: feat,
      bbox: matchEntry.bbox,
      centroid: matchEntry.centroid,
      layerKey: matchEntry.overlayKey,
      id: String(matchEntry.props.id || matchEntry.props.name || ""),
      name: String(
        matchEntry.props.name ||
          matchEntry.props.era_label ||
          matchEntry.props.historic_district ||
          "Isolated Area"
      ),
      mode: validMode,
    };
    this.selectedBoundaryFeature = feat;

    if (opts.flyTo) {
      const [minLng, minLat, maxLng, maxLat] = matchEntry.bbox;
      const isSmallBoundary =
        matchEntry.overlayKey === "platted_subdivisions" ||
        matchEntry.overlayKey === "neighborhoods" ||
        matchEntry.overlayKey === "historic_districts" ||
        matchEntry.overlayKey === "heritage_districts" ||
        matchEntry.overlayKey === "potential_historic_districts";
      if (!this.useCanvasFallback && this.map && typeof this.map.fitBounds === "function") {
        try {
          this.map.fitBounds(
            [
              [minLng, minLat],
              [maxLng, maxLat],
            ],
            {
              padding: { top: 90, bottom: 110, left: 360, right: 380 },
              maxZoom:
                matchEntry.overlayKey === "platted_subdivisions"
                  ? 16.2
                  : isSmallBoundary
                  ? 15.6
                  : 14.2,
              duration: 850,
            }
          );
        } catch (_e) {
          this.flyToLocation({
            lng: matchEntry.centroid[0],
            lat: matchEntry.centroid[1],
            zoom: isSmallBoundary ? 15.0 : 13.5,
          });
        }
      } else {
        this.flyToLocation({
          lng: matchEntry.centroid[0],
          lat: matchEntry.centroid[1],
          zoom: isSmallBoundary ? 15.0 : 13.5,
        });
      }
    }

    if (typeof this.onIsolationChange === "function") {
      this.onIsolationChange(this.isolatedBoundary);
    }
    return this.isolatedBoundary;
  }

  _syncIsolatedBoundaryWithState(state) {
    const spec = state?.isolatedBoundary || null;
    if (!spec) {
      if (this.isolatedBoundary) {
        this.isolatedBoundary = null;
        if (typeof this.onIsolationChange === "function") {
          this.onIsolationChange(null);
        }
      }
      return;
    }
    const cur = this.isolatedBoundary;
    const sameTarget =
      cur &&
      cur.layerKey === this._normalizeBoundaryOverlayKey(spec.layerKey || "") &&
      (cur.id === spec.id || cur.name.toLowerCase() === String(spec.name || "").toLowerCase());
    if (sameTarget) {
      const nextMode =
        spec.mode === "footprints_only" || spec.mode === "border_only"
          ? spec.mode
          : "contents";
      if (cur.mode !== nextMode) {
        cur.mode = nextMode;
        if (typeof this.onIsolationChange === "function") {
          this.onIsolationChange(cur);
        }
      }
      return;
    }
    const shouldInitialFly = !this._didInitialIsolateFlyTo;
    this._didInitialIsolateFlyTo = true;
    this._resolveIsolatedBoundaryFromSpec(spec, { flyTo: shouldInitialFly });
  }

  setIsolatedBoundary(spec, opts = {}) {
    if (!spec) {
      this.clearIsolatedBoundary();
      return null;
    }
    const resolved = this._resolveIsolatedBoundaryFromSpec(spec, {
      flyTo: opts.flyTo !== false,
    });
    if (!resolved) return null;
    const state = this.filterStore.getState();
    const nextPatch = {
      isolatedBoundary: {
        layerKey: resolved.layerKey,
        id: resolved.id,
        name: resolved.name,
        mode: resolved.mode,
      },
    };
    if (resolved.mode !== "border_only" && state.renderMode === "none") {
      nextPatch.renderMode = state.lastActiveRenderMode || "buildings";
    }
    this.filterStore.setState(nextPatch);
    return resolved;
  }

  setIsolationMode(mode = "contents") {
    if (!this.isolatedBoundary) return;
    const validMode =
      mode === "footprints_only" || mode === "border_only" ? mode : "contents";
    this.isolatedBoundary.mode = validMode;
    const state = this.filterStore.getState();
    const nextPatch = {
      isolatedBoundary: {
        layerKey: this.isolatedBoundary.layerKey,
        id: this.isolatedBoundary.id,
        name: this.isolatedBoundary.name,
        mode: validMode,
      },
    };
    if (validMode !== "border_only" && state.renderMode === "none") {
      nextPatch.renderMode = state.lastActiveRenderMode || "buildings";
    }
    this.filterStore.setState(nextPatch);
    if (typeof this.onIsolationChange === "function") {
      this.onIsolationChange(this.isolatedBoundary);
    }
  }

  clearIsolatedBoundary() {
    if (!this.isolatedBoundary && !this.filterStore.getState().isolatedBoundary) return;
    this.isolatedBoundary = null;
    this.filterStore.setState({ isolatedBoundary: null });
    if (typeof this.onIsolationChange === "function") {
      this.onIsolationChange(null);
    }
  }

  getIsolatedBoundary() {
    return this.isolatedBoundary;
  }

  captureMapScreenshotDataUrl() {
    try {
      if (this.useCanvasFallback && this.canvasState?.canvas) {
        this._renderCanvas2D();
        return this.canvasState.canvas.toDataURL("image/png");
      }
      if (this.map) {
        this.map.triggerRepaint();
        return this.map.getCanvas().toDataURL("image/png");
      }
    } catch (err) {
      console.warn("Map canvas screenshot capture failed:", err);
    }
    return null;
  }

  /**
   * Collects vector boundary geometry, building footprint polygons, and landmark/Good Brick points
   * for the Vector SVG, Print, T-Shirt & Coaster Studio (`#export-studio-modal`).
   */
  collectVectorFeaturesForExport(options = {}) {
    const state = this.filterStore.getState();
    let targetEntry = null;
    let boundaryFeature = null;

    if (options.boundarySpec) {
      targetEntry = this._findBoundaryEntry(options.boundarySpec);
      if (targetEntry) {
        boundaryFeature = {
          type: "Feature",
          geometry: targetEntry.feature.geometry,
          properties: {
            ...targetEntry.props,
            overlay_layer: targetEntry.overlayKey,
            is_boundary_feature: true,
          },
        };
      }
    } else if (options.useViewportOnly) {
      boundaryFeature = null;
    } else if (this.isolatedBoundary?.feature) {
      boundaryFeature = this.isolatedBoundary.feature;
      targetEntry = {
        bbox: this.isolatedBoundary.bbox,
        centroid: this.isolatedBoundary.centroid,
        overlayKey: this.isolatedBoundary.layerKey,
        props: boundaryFeature.properties || {},
      };
    } else if (this.selectedBoundaryFeature?.geometry) {
      boundaryFeature = this.selectedBoundaryFeature;
      const meta = this._computeGeometryBBoxAndCentroid(boundaryFeature.geometry);
      targetEntry = {
        bbox: meta ? meta.bbox : [-95.4, 29.74, -95.35, 29.79],
        centroid: meta ? [meta.lng, meta.lat] : [-95.38, 29.766],
        overlayKey: boundaryFeature.properties?.overlay_layer || "neighborhoods",
        props: boundaryFeature.properties || {},
      };
    }

    let clipBBox = null;
    let centroid = [-95.3805, 29.7662];
    const boundaryGeom = boundaryFeature?.geometry || null;

    if (boundaryGeom && targetEntry) {
      clipBBox = targetEntry.bbox;
      centroid = targetEntry.centroid;
    } else {
      const bounds = this.useCanvasFallback
        ? this._getCanvasBounds()
        : this.map
        ? this.map.getBounds()
        : null;
      if (bounds) {
        clipBBox = [
          bounds.getWest(),
          bounds.getSouth(),
          bounds.getEast(),
          bounds.getNorth(),
        ];
        centroid = [
          (clipBBox[0] + clipBBox[2]) / 2,
          (clipBBox[1] + clipBBox[3]) / 2,
        ];
      } else {
        clipBBox = [-95.4, 29.74, -95.35, 29.79];
      }
    }

    const [minLng, minLat, maxLng, maxLat] = clipBBox;
    const isPointInside = (lng, lat) => {
      if (lng < minLng || lng > maxLng || lat < minLat || lat > maxLat) return false;
      if (!boundaryGeom) return true;
      return this._pointInPolygonGeometry(lng, lat, boundaryGeom);
    };

    const getPolySamplePoint = (geom) => {
      if (!geom || !geom.coordinates) return null;
      const ring =
        geom.type === "Polygon"
          ? geom.coordinates[0]
          : geom.type === "MultiPolygon" && geom.coordinates[0]
          ? geom.coordinates[0][0]
          : null;
      if (!ring || !ring.length) return null;
      let sx = 0;
      let sy = 0;
      let n = 0;
      const step = Math.max(1, Math.floor(ring.length / 6));
      for (let i = 0; i < ring.length; i += step) {
        const pt = ring[i];
        if (Array.isArray(pt) && Number.isFinite(pt[0]) && Number.isFinite(pt[1])) {
          sx += pt[0];
          sy += pt[1];
          n += 1;
        }
      }
      return n > 0 ? [sx / n, sy / n] : null;
    };

    const buildingsByKey = new Map();

    const addBuildingCandidate = (feat, isAuthoritativeSource = false) => {
      if (!feat?.geometry) return;
      const gType = feat.geometry.type;
      if (gType !== "Polygon" && gType !== "MultiPolygon") return;
      const p = applyOverrideToProperties(feat.properties || {}, this.curatedOverrides);
      if (p.suppress_only) return;
      if (options.respectYearFilter !== false && !featureMatchesFilter(p, state)) return;

      const samplePt = getPolySamplePoint(feat.geometry);
      if (!samplePt || !isPointInside(samplePt[0], samplePt[1])) return;

      const coordFallback = `${samplePt[0].toFixed(5)},${samplePt[1].toFixed(5)}`;
      const key = this._getCanonicalBuildingKey(p, coordFallback);
      const areaDeg2 = this._approxPolygonAreaDeg2(feat.geometry);

      const existing = buildingsByKey.get(key);
      if (existing) {
        if (!existing._authoritative && areaDeg2 > existing._areaDeg2 * 1.05) {
          existing.geometry = feat.geometry;
          existing._areaDeg2 = areaDeg2;
        }
        return;
      }

      buildingsByKey.set(key, {
        type: "Feature",
        geometry: feat.geometry,
        _areaDeg2: areaDeg2,
        _authoritative: isAuthoritativeSource,
        properties: {
          ...p,
          _export_color: evaluateFeatureColor(p, state.colorMode, state.paletteStyle),
        },
      });
    };

    for (const f of this.overridesFC?.features || []) addBuildingCandidate(f, true);
    for (const f of this.buildingsData || []) addBuildingCandidate(f, true);

    if (!this.useCanvasFallback && this.map) {
      const queryLayers = [
        ...(this.buildingFillLayerIds || []),
        ...(this.buildingExtrusionLayerIds || []),
      ].filter((id) => this.map.getLayer(id));
      if (queryLayers.length > 0) {
        try {
          const rendered = this.map.queryRenderedFeatures({ layers: queryLayers });
          for (const f of rendered) addBuildingCandidate(f, false);
        } catch (_e) {}
      }
      if (Array.isArray(this.shardSourceIds)) {
        for (const srcId of this.shardSourceIds) {
          try {
            const srcFeats = this.map.querySourceFeatures(srcId, { sourceLayer: "buildings" });
            for (const f of srcFeats) addBuildingCandidate(f, false);
          } catch (_e) {}
        }
      }
    }

    if (buildingsByKey.size === 0 && Array.isArray(this.parcelsData)) {
      for (const f of this.parcelsData) addBuildingCandidate(f, false);
    }

    const buildings = Array.from(buildingsByKey.values());

    const landmarks = [];
    for (const f of this.overlaysData?.landmarks?.features || []) {
      const c = f.geometry?.coordinates;
      if (Array.isArray(c) && isPointInside(c[0], c[1])) {
        landmarks.push(f);
      }
    }

    const goodBricks = [];
    for (const f of this.overlaysData?.good_brick_awards?.features || []) {
      const c = f.geometry?.coordinates;
      if (Array.isArray(c) && isPointInside(c[0], c[1])) {
        goodBricks.push(f);
      }
    }

    const years = buildings
      .map((b) => Number(b.properties?.year_built) || 0)
      .filter((y) => y >= 1836 && y <= 2026)
      .sort((a, b) => a - b);

    const bProps = boundaryFeature?.properties || {};
    const earliestYear =
      years.length > 0
        ? years[0]
        : Number(bProps.earliest_year) >= 1836
        ? Number(bProps.earliest_year)
        : 0;
    const medianYear =
      years.length > 0
        ? years[Math.floor(years.length / 2)]
        : Number(bProps.median_year) >= 1836
        ? Number(bProps.median_year)
        : 0;
    const pre1940Count =
      years.length > 0
        ? years.filter((y) => y < 1940).length
        : Number(bProps.pre_1940_count) || 0;

    const boundaryName = boundaryFeature
      ? String(
          bProps.name ||
            bProps.era_label ||
            bProps.historic_district ||
            "Houston Historic District"
        ).trim()
      : "Houston Custom Map View";

    const boundaryLayerKey = boundaryFeature
      ? String(bProps.overlay_layer || "neighborhoods")
      : "viewport";

    return {
      scope: boundaryFeature ? "boundary" : "viewport",
      isIsolatedPolygon: Boolean(boundaryFeature),
      boundaryFeature,
      boundaryName,
      boundaryLayerKey,
      bbox: clipBBox,
      centroid,
      buildings,
      landmarks,
      goodBricks,
      stats: {
        buildingCount: buildings.length || Number(bProps.building_count) || 0,
        extractedFootprintCount: buildings.length,
        earliestYear,
        medianYear,
        pre1940Count,
        landmarkCount: landmarks.length || Number(bProps.landmark_count) || 0,
        goodBrickCount: goodBricks.length || Number(bProps.good_brick_count) || 0,
      },
    };
  }

  getRenderedBuildingCandidates(limit = 500) {
    const out = [];
    const seenKeys = new Set();

    const extractCentroid = (feat) => {
      if (!feat) return null;
      if (feat._centroid) return feat._centroid;
      const geom = feat.geometry;
      if (!geom || !geom.coordinates) return null;
      let res = null;
      if (geom.type === "Point" && Array.isArray(geom.coordinates)) {
        res = [Number(geom.coordinates[0]), Number(geom.coordinates[1])];
      } else {
        const ring =
          geom.type === "Polygon"
            ? geom.coordinates[0]
            : geom.type === "MultiPolygon" && geom.coordinates[0]
            ? geom.coordinates[0][0]
            : null;
        if (Array.isArray(ring) && ring.length) {
          let sx = 0;
          let sy = 0;
          let n = 0;
          for (const pt of ring) {
            if (Array.isArray(pt) && Number.isFinite(pt[0]) && Number.isFinite(pt[1])) {
              sx += pt[0];
              sy += pt[1];
              n += 1;
            }
          }
          if (n > 0) res = [sx / n, sy / n];
        }
      }
      if (res) feat._centroid = res;
      return res;
    };

    if (!this.useCanvasFallback && this.map && this.map.getZoom() >= 13.5) {
      const queryLayers = [
        "curated-overrides-fill",
        "curated-overrides-extrusion",
        ...(this.buildingFillLayerIds || []),
        ...(this.buildingExtrusionLayerIds || []),
      ].filter((id) => this.map.getLayer(id));

      if (queryLayers.length > 0) {
        const rendered = this.map.queryRenderedFeatures({ layers: queryLayers });
        for (const feat of rendered) {
          if (out.length >= limit) break;
          const p = applyOverrideToProperties(feat.properties || {}, this.curatedOverrides);
          if (p.suppress_only) continue;
          const key = this._getCanonicalBuildingKey(p);
          if (key) {
            if (seenKeys.has(key)) continue;
            seenKeys.add(key);
          }
          const pt = extractCentroid(feat);
          if (!pt) continue;
          out.push({
            props: p,
            lon: pt[0],
            lat: pt[1],
            inViewport: true,
          });
        }
      }
    }

    return out;
  }
}

