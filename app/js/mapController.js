/**
 * MapLibre GL JS + PMTiles controller for The Houston Building Atlas v2,
 * with automatic high-FPS HTML5 2D Canvas Vector + Slippy Tile fallback
 * for environments where WebGL context creation is unavailable.
 */

import {
  buildColorExpression,
  getYearColorHex,
  PRESERVATION_STATUS_ITEMS,
  USE_CATEGORY_ITEMS,
} from "./palettes.js?v=20261007f";
import {
  buildAnnexationFilterExpression,
  buildFeatureFilterExpression,
  featureMatchesFilter,
  resolveActiveAnnexationDecade,
} from "./filterStore.js?v=20261007f";
import {
  applyOverrideToProperties,
  loadCuratedOverrides,
} from "./curatedEdits.js?v=20261008p";
import { fetchHcadLiveRecord } from "./hcadLink.js?v=20261008p";

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

function evaluateFeatureColor(props, colorMode, paletteStyle) {
  if (!props) return "#524E4A";
  if (colorMode === "preservation_status") {
    if (props.landmark_type === "Protected Landmark") return "#E63946";
    if (props.landmark_type === "Landmark") return "#F4A261";
    if (props.contributing === "Contributing") return "#2A9D8F";
    if (props.contributing === "Non-Contributing") return "#E76F51";
    return "#5E6472";
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
  }) {
    this.containerId = containerId;
    this.filterStore = filterStore;
    this.onSelectFeature = onSelectFeature;
    this.onViewportStats = onViewportStats;
    this.onPitchChange = onPitchChange || null;
    this.onSelectTourStop = onSelectTourStop || null;
    this.map = null;
    this.popup = null;
    this.useCanvasFallback = false;
    this.canvasState = null;
    this.buildingsData = [];
    this.parcelsData = [];
    this.overlaysData = null;
    this.curatedOverrides = {};
    this.sheetSyncStatus = null;
    this.overridesFC = { type: "FeatureCollection", features: [] };
    this.selectedFeatureId = null;
    this.pmtilesManifest = null;
    this.buildingFillLayerIds = ["buildings-fill"];
    this.buildingLineLayerIds = ["buildings-line"];
    this.buildingExtrusionLayerIds = ["buildings-extrusion"];
    this.highlightLayerIds = ["selected-feature-highlight"];
    this.activeTour = null;
    this.activeTourStopIndex = -1;
    this.tourStopMarkers = [];
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
    this.syncWithState(this.filterStore.getState());
    this.computeViewportHistogram();

    this.filterStore.subscribe((nextState) => {
      this.syncWithState(nextState);
      this.computeViewportHistogram();
    });
  }

  async _fetchDataPayloads() {
    const [buildingsRes, parcelsRes, overlaysRes, manifestRes, overridesResult] =
      await Promise.all([
        fetch("public/data/buildings.geojson?v=20261008p"),
        fetch("public/data/parcels.geojson?v=20261008p"),
        fetch("public/data/overlays.json?v=20261008p"),
        fetch("public/data/pmtiles_manifest.json?v=20261008p").catch(() => null),
        loadCuratedOverrides(),
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

    this.curatedOverrides = (overridesResult && overridesResult.overrides) || {};
    this.sheetSyncStatus = (overridesResult && overridesResult.syncStatus) || null;

    this.buildingsFC = buildingsFC;
    this.parcelsFC = parcelsFC;
    this.buildingsData = buildingsFC.features || [];
    this.parcelsData = parcelsFC.features || [];
    this.overlaysData = overlays;

    this._applyCuratedOverridesInMemory();
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
      attributionControl: false,
      style: {
        version: 8,
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
            tiles: BASEMAP_TILES.historic_topo.tiles,
            tileSize: 256,
            maxzoom: 15,
            attribution: BASEMAP_TILES.historic_topo.attribution,
          },
          "openfreemap-vector": {
            type: "vector",
            url: "https://tiles.openfreemap.org/planet",
          },
        },
        layers: [
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
              visibility: state.layers?.historicMap ? "visible" : "none",
            },
            paint: {
              "raster-opacity": Math.max(
                0.1,
                Math.min(1, (Number(state.historicMapOpacity) || 75) / 100)
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
  }

  _addMapLibreSourcesAndLayers() {
    const overlays = this.overlaysData;
    const pmtilesUrl = new URL("public/data/houston_atlas.pmtiles", window.location.href).href;
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
      const shardUrl = new URL(`public/data/${shardFiles[i]}`, window.location.href).href;
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
      data: overlays.annexations || { type: "FeatureCollection", features: [] },
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

    const state = this.filterStore.getState();
    const colorExpr = buildColorExpression(state.colorMode, state.paletteStyle);
    const filterExpr = buildFeatureFilterExpression(state);
    const shardFilterExpr = this._buildShardLayerFilter(filterExpr);

    this.map.addLayer({
      id: "annexations-fill",
      type: "fill",
      source: "annexations-src",
      paint: { "fill-color": "#D97706", "fill-opacity": 0.08 },
    });
    this.map.addLayer({
      id: "annexations-line",
      type: "line",
      source: "annexations-src",
      paint: {
        "line-color": "#F59E0B",
        "line-width": 1.8,
        "line-dasharray": [4, 3],
        "line-opacity": 0.75,
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
            "fill-extrusion-opacity": 0.9,
          },
        });
        this.map.addLayer({
          id: hlId,
          type: "line",
          source: srcId,
          "source-layer": "buildings",
          filter: ["==", ["get", "id"], ""],
          paint: { "line-color": "#FDE047", "line-width": 3.2 },
        });
      }
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
        id: "buildings-extrusion",
        type: "fill-extrusion",
        source: "buildings-src",
        filter: shardFilterExpr,
        layout: { visibility: state.extrude3D ? "visible" : "none" },
        paint: {
          "fill-extrusion-color": colorExpr,
          "fill-extrusion-height": extrusionHeightExpr,
          "fill-extrusion-base": 0,
          "fill-extrusion-opacity": 0.9,
        },
      });
      this.map.addLayer({
        id: "selected-feature-highlight",
        type: "line",
        source: "buildings-src",
        filter: ["==", ["get", "id"], ""],
        paint: { "line-color": "#FDE047", "line-width": 3.2 },
      });
    }

    // Curated Overrides Layer (renders live Google Sheet / Preservation Houston verified edits on top)
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
      id: "curated-overrides-extrusion",
      type: "fill-extrusion",
      source: "curated-overrides-src",
      filter: filterExpr,
      layout: { visibility: state.extrude3D ? "visible" : "none" },
      paint: {
        "fill-extrusion-color": colorExpr,
        "fill-extrusion-height": extrusionHeightExpr,
        "fill-extrusion-base": 0,
        "fill-extrusion-opacity": 0.94,
      },
    });
    this.map.addLayer({
      id: "curated-overrides-highlight",
      type: "line",
      source: "curated-overrides-src",
      filter: ["==", ["get", "id"], ""],
      paint: { "line-color": "#FDE047", "line-width": 3.4 },
    });
    this.highlightLayerIds.push("curated-overrides-highlight");

    // Dedicated single-feature selection source so clicking a building on a multi-building
    // campus parcel (e.g. Rice University) highlights ONLY the clicked building polygon.
    this.map.addSource("selected-feature-src", {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
    });
    this.map.addLayer({
      id: "selected-feature-outline",
      type: "line",
      source: "selected-feature-src",
      paint: { "line-color": "#FDE047", "line-width": 3.5 },
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
    this.map.addLayer({
      id: "landmarks-circle",
      type: "circle",
      source: "landmarks-src",
      paint: {
        "circle-radius": 6.0,
        "circle-color": [
          "case",
          ["==", ["get", "designation"], "Protected Landmark"],
          "#E63946",
          "#F4A261",
        ],
        "circle-stroke-color": "#FFFBEB",
        "circle-stroke-width": 1.6,
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

    // Guided Walking Tour Route Source & Layers
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
      if (ov.suppress_only || !ov.is_building_override || ov.replace_parcel_shards) {
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
    const getClickLayers = () => [
      "good-brick-circle",
      "landmarks-circle",
      "thc-markers-circle",
      "curated-overrides-extrusion",
      "curated-overrides-fill",
      ...this.buildingExtrusionLayerIds,
      ...this.buildingFillLayerIds,
      "parcels-fill",
    ];

    const getHoverLayers = () => {
      const base = [...getClickLayers()];
      if (this.filterStore.getState().renderMode === "none") {
        base.push(
          "historic-districts-fill",
          "heritage-districts-fill",
          "nrhp-districts-fill",
          "annexations-fill"
        );
      }
      return base;
    };

    this.map.on("mousemove", (e) => {
      const activeLayers = getHoverLayers().filter((id) => this.map.getLayer(id));
      const features = this.map.queryRenderedFeatures(e.point, { layers: activeLayers });
      if (!features.length) {
        this.map.getCanvas().style.cursor = "";
        this.popup.remove();
        return;
      }
      this.map.getCanvas().style.cursor = "pointer";
      const p = applyOverrideToProperties(features[0].properties || {}, this.curatedOverrides);
      this.popup
        .setLngLat(e.lngLat)
        .setHTML(this._buildTooltipHTML(p))
        .addTo(this.map);
    });

    this.map.on("click", (e) => {
      const activeLayers = getClickLayers().filter((id) => this.map.getLayer(id));
      const features = this.map.queryRenderedFeatures(e.point, { layers: activeLayers });
      if (!features.length) return;
      const top = features[0];
      const p = applyOverrideToProperties(top.properties || {}, this.curatedOverrides);
      if (
        (top.layer.id === "landmarks-circle" || top.layer.id === "good-brick-circle") &&
        (p.hcad_num || p.building_id)
      ) {
        // Check if there is an exact building_id override, an underlying building polygon at the clicked point, in overridesFC, or in buildingsData
        const bldHit =
          (p.building_id &&
            (this.overridesFC?.features || []).find(
              (f) =>
                f.properties &&
                (f.properties.building_id === p.building_id || f.properties.id === p.building_id)
            )) ||
          features.find(
            (f) =>
              f.layer.id !== "landmarks-circle" &&
              f.layer.id !== "good-brick-circle" &&
              f.layer.id !== "thc-markers-circle" &&
              f.layer.id !== "historic-districts-fill" &&
              f.layer.id !== "heritage-districts-fill" &&
              f.layer.id !== "nrhp-districts-fill" &&
              f.layer.id !== "annexations-fill"
          ) ||
          (p.hcad_num &&
            (this.overridesFC?.features || []).find(
              (f) => f.properties && f.properties.hcad_num === p.hcad_num
            )) ||
          (p.hcad_num &&
            this.buildingsData.find(
              (f) => f.properties && f.properties.hcad_num === p.hcad_num
            ));
        if (bldHit) {
          const baseBldProps = applyOverrideToProperties(
            bldHit.properties || {},
            this.curatedOverrides
          );
          const mergedClickProps = {
            ...baseBldProps,
            landmark_name: p.landmark_name || baseBldProps.landmark_name || p.name || "",
            good_brick_awards: p.good_brick_awards || baseBldProps.good_brick_awards || null,
            good_brick_summary: p.good_brick_summary || baseBldProps.good_brick_summary || "",
            good_brick_years: p.good_brick_years || baseBldProps.good_brick_years || "",
          };
          this.highlightAndInspectFeature(mergedClickProps, bldHit.geometry || null);
          return;
        }
      }
      this.highlightAndInspectFeature(p, top.geometry || null);
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

    this.map.on("moveend", () => {
      this._captureDynamicOverrideGeometries();
      this.computeViewportHistogram();
    });

    this.map.on("idle", () => {
      this._captureDynamicOverrideGeometries();
    });
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
      const hit = this._hitTestCanvas2D(px, py);
      if (hit) {
        canvas.style.cursor = "pointer";
        tooltipEl.innerHTML = `<div class="maplibregl-popup-content">${this._buildTooltipHTML(hit)}</div>`;
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
        const hit = this._hitTestCanvas2D(e.clientX - rect.left, e.clientY - rect.top);
        if (hit) {
          if (hit.isTourStop && typeof this.onSelectTourStop === "function") {
            this.onSelectTourStop(hit.stopIndex);
          } else {
            this.highlightAndInspectFeature(hit);
          }
        }
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

    ctx.save();
    ctx.scale(dpr, dpr);

    // 1. Background & Basemap Slippy Tiles
    ctx.fillStyle = state.basemap === "warm_parchment" ? "#EBE6DC" : "#0D1016";
    ctx.fillRect(0, 0, width, height);

    const baseKey = state.basemap || "dark_archival";
    const baseConfig = BASEMAP_TILES[baseKey] || BASEMAP_TILES.dark_archival;
    const maxTileZ = baseConfig.maxZoom || 16;
    const tileZ = Math.max(10, Math.min(maxTileZ, Math.floor(cs.zoom)));
    const zoomFactor = Math.pow(2, cs.zoom - tileZ);
    const tileSizeScreen = 256 * zoomFactor;

    const centerTileX = ((cs.lng + 180) / 360) * Math.pow(2, tileZ);
    const sinLat = Math.sin((cs.lat * Math.PI) / 180);
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

    if (state.layers?.historicMap && BASEMAP_TILES.historic_topo) {
      const histConfig = BASEMAP_TILES.historic_topo;
      const histZ = Math.max(10, Math.min(histConfig.maxZoom || 15, Math.floor(cs.zoom)));
      const histFactor = Math.pow(2, cs.zoom - histZ);
      const histSizeScreen = 256 * histFactor;
      const histCenterX = ((cs.lng + 180) / 360) * Math.pow(2, histZ);
      const histCenterY =
        (0.5 - Math.log((1 + sinLat) / (1 - sinLat)) / (4 * Math.PI)) * Math.pow(2, histZ);
      const hCols = Math.ceil(width / histSizeScreen / 2) + 1;
      const hRows = Math.ceil(height / histSizeScreen / 2) + 1;
      const histTpl = histConfig.tiles[0];
      ctx.save();
      ctx.globalAlpha = Math.max(0.1, Math.min(1, (Number(state.historicMapOpacity) || 75) / 100));
      for (let dx = -hCols; dx <= hCols; dx++) {
        for (let dy = -hRows; dy <= hRows; dy++) {
          const tx = Math.floor(histCenterX) + dx;
          const ty = Math.floor(histCenterY) + dy;
          const maxT = 1 << histZ;
          if (tx < 0 || ty < 0 || tx >= maxT || ty >= maxT) continue;
          const sx = width / 2 + (tx - histCenterX) * histSizeScreen;
          const sy = height / 2 + (ty - histCenterY) * histSizeScreen;
          const hCacheId = `hist_topo:${histZ}/${tx}/${ty}`;
          let hImg = cs.tileCache.get(hCacheId);
          if (!hImg) {
            hImg = new Image();
            hImg.crossOrigin = "anonymous";
            hImg.src = histTpl.replace("{z}", histZ).replace("{x}", tx).replace("{y}", ty);
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

    // 2. Annexation History Overlay
    if ((state.layers.annexations || state.syncAnnexationToTime) && this.overlaysData?.annexations) {
      const activeDecade = resolveActiveAnnexationDecade(state);
      for (const feat of this.overlaysData.annexations.features || []) {
        const dec = Number(feat.properties?.decade) || 1836;
        if (activeDecade !== null && dec !== activeDecade) continue;
        drawPolygonFeature(
          feat.geometry,
          "rgba(217, 119, 6, 0.08)",
          "rgba(245, 158, 11, 0.78)",
          1.8,
          [5, 4]
        );
      }
    }

    // 3. NRHP, Heritage, and COH Historic Districts
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

    cs.renderedBBoxes = [];

    // 4. Tax Parcels Layer
    const showBuildings = state.renderMode === "buildings" || state.renderMode === "both";
    const showParcelsFill = state.renderMode === "parcels";
    const showParcelsLine = state.renderMode === "both" || state.renderMode === "parcels";

    if (showParcelsFill || showParcelsLine) {
      for (const feat of this.parcelsData) {
        const p = feat.properties || {};
        if (!featureMatchesFilter(p, state)) continue;
        const fill = showParcelsFill
          ? evaluateFeatureColor(p, state.colorMode, state.paletteStyle)
          : null;
        const stroke = showParcelsLine ? "rgba(148, 163, 184, 0.42)" : null;
        const bbox = drawPolygonFeature(feat.geometry, fill, stroke, 0.7);
        if (bbox && showParcelsFill) {
          cs.renderedBBoxes.push({ ...bbox, props: p });
        }
      }
    }

    // 5. Building Footprints & Curated Overrides (2D or 3D Isometric Extrusion)
    if (showBuildings) {
      const seenCanvasIds = new Set();
      const allBuildingFeatures = [
        ...(this.overridesFC?.features || []),
        ...this.buildingsData,
      ];
      for (const feat of allBuildingFeatures) {
        const p = feat.properties || {};
        const fid = p.id || p.building_id || "";
        if (fid) {
          if (seenCanvasIds.has(fid)) continue;
          seenCanvasIds.add(fid);
        }
        if (!featureMatchesFilter(p, state)) continue;
        const color = evaluateFeatureColor(p, state.colorMode, state.paletteStyle);
        const isSelected =
          this.selectedFeatureId &&
          (p.id === this.selectedFeatureId || p.building_id === this.selectedFeatureId);
        const stroke = isSelected ? "#FDE047" : "rgba(15, 17, 21, 0.72)";
        const lw = isSelected ? 2.8 : 0.75;
        const extrudeM = state.extrude3D ? Number(p.height_m) || 5.0 : 0;

        const bbox = drawPolygonFeature(feat.geometry, color, stroke, lw, null, extrudeM);
        if (bbox) {
          cs.renderedBBoxes.push({ ...bbox, props: p });
        }
      }
    }

    // 6. THC Markers
    if (state.layers.thcMarkers && this.overlaysData?.thc_markers) {
      for (const feat of this.overlaysData.thc_markers.features || []) {
        const coords = feat.geometry?.coordinates;
        if (!coords) continue;
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
        });
      }
    }

    // 7. COH Designated Landmarks (LM & PLM)
    if (state.layers.landmarks && this.overlaysData?.landmarks) {
      for (const feat of this.overlaysData.landmarks.features || []) {
        const coords = feat.geometry?.coordinates;
        if (!coords) continue;
        const [sx, sy] = this._lngLatToScreen(coords[0], coords[1], width, height);
        if (sx < 0 || sx > width || sy < 0 || sy > height) continue;
        const isPLM = feat.properties?.designation === "Protected Landmark";
        ctx.beginPath();
        ctx.arc(sx, sy, 5.8, 0, Math.PI * 2);
        ctx.fillStyle = isPLM ? "#E63946" : "#F4A261";
        ctx.fill();
        ctx.lineWidth = 1.6;
        ctx.strokeStyle = "#FFFBEB";
        ctx.stroke();
        cs.renderedBBoxes.push({
          minX: sx - 7,
          minY: sy - 7,
          maxX: sx + 7,
          maxY: sy + 7,
          props: feat.properties,
        });
      }
    }

    // 8. Preservation Houston Good Brick Award Winners (1979–2026)
    if (state.layers.goodBrickAwards && this.overlaysData?.good_brick_awards) {
      for (const feat of this.overlaysData.good_brick_awards.features || []) {
        const coords = feat.geometry?.coordinates;
        if (!coords) continue;
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
        });
      }
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
  }

  /* ========================================================================
     Unified Public Controller API
     ======================================================================== */
  _buildTooltipHTML(p) {
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
        ? `<div class="tooltip-aka">AKA: ${altList.slice(0, 2).join(", ")}</div>`
        : "";

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
      badge = p.designation;
    } else if (p.marker_num) {
      badge = `THC Marker #${p.marker_num}`;
    } else if (p.type) {
      badge = p.type;
    } else if (p.era_label) {
      badge = "Houston Annexation History";
    } else {
      badge = p.use_category || "Undated Parcel";
    }
    const subtitle =
      (p.address && p.address !== title ? `${p.address}${p.historic_district && p.historic_district !== "Outside City District" && p.historic_district !== "Outside Historic District" ? ` • ${p.historic_district}` : ""}` : "") ||
      p.historic_district ||
      p.address ||
      p.subdivision ||
      (p.era_label && p.decade ? `Annexed in the ${p.decade}s` : "") ||
      (p.type ? "Preservation District Boundary" : "Click to inspect property record");

    const goodBrickPill =
      p.good_brick_summary && badge !== `★ ${p.good_brick_summary}`
        ? `<span class="tooltip-good-brick">&#9733; ${p.good_brick_summary}</span>`
        : "";

    return `<div class="tooltip-card">
      <div class="tooltip-top">
        <span class="tooltip-badge">${badge}</span>
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
    </div>`;
  }

  syncWithState(state) {
    if (!this.isReady) return;
    if (this.useCanvasFallback) {
      this._renderCanvas2D();
      return;
    }
    if (!this.map) return;

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
        state.layers?.historicMap ? "visible" : "none"
      );
      const histOpacity = Math.max(
        0.1,
        Math.min(1, (Number(state.historicMapOpacity) || 75) / 100)
      );
      this.map.setPaintProperty("basemap-historic-topo-layer", "raster-opacity", histOpacity);
    }

    const colorExpr = buildColorExpression(state.colorMode, state.paletteStyle);
    const filterExpr = buildFeatureFilterExpression(state);
    const shardFilterExpr = this._buildShardLayerFilter(filterExpr);

    for (const layerId of [
      ...this.buildingFillLayerIds,
      ...this.buildingLineLayerIds,
      ...this.buildingExtrusionLayerIds,
    ]) {
      if (this.map.getLayer(layerId)) {
        this.map.setFilter(layerId, shardFilterExpr);
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

    for (const fillId of [...this.buildingFillLayerIds, "curated-overrides-fill"]) {
      if (this.map.getLayer(fillId)) {
        this.map.setPaintProperty(fillId, "fill-color", colorExpr);
      }
    }
    for (const extId of [...this.buildingExtrusionLayerIds, "curated-overrides-extrusion"]) {
      if (this.map.getLayer(extId)) {
        this.map.setPaintProperty(extId, "fill-extrusion-color", colorExpr);
      }
    }
    if (this.map.getLayer("parcels-fill")) {
      this.map.setPaintProperty("parcels-fill", "fill-color", colorExpr);
    }

    const showBuildings = state.renderMode === "buildings" || state.renderMode === "both";
    const showParcelsFill = state.renderMode === "parcels";
    const showParcelsLine = state.renderMode === "both" || state.renderMode === "parcels";

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
      [...this.buildingLineLayerIds, "curated-overrides-line"],
      showBuildings && !state.extrude3D
    );
    setVis(
      [...this.buildingExtrusionLayerIds, "curated-overrides-extrusion"],
      showBuildings && state.extrude3D
    );
    setVis(["parcels-fill"], showParcelsFill);
    setVis(["parcels-line"], showParcelsLine);

    setVis(["good-brick-glow", "good-brick-circle"], state.layers.goodBrickAwards);
    setVis(["landmarks-circle"], state.layers.landmarks);
    setVis(["historic-districts-fill", "historic-districts-line"], state.layers.historicDistricts);
    setVis(["heritage-districts-fill", "heritage-districts-line"], state.layers.heritageDistricts);
    setVis(["nrhp-districts-fill", "nrhp-districts-line"], state.layers.nrhpDistricts);
    setVis(["thc-markers-circle"], state.layers.thcMarkers);

    const showAnnex = state.layers.annexations || state.syncAnnexationToTime;
    setVis(["annexations-fill", "annexations-line"], showAnnex);
    if (showAnnex) {
      const annexFilter = buildAnnexationFilterExpression(state);
      this.map.setFilter("annexations-fill", annexFilter);
      this.map.setFilter("annexations-line", annexFilter);
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

  selectFeatureByIdOrHcad({ hcadNum = "", featureId = "", flyTo = false }) {
    if (!featureId && !hcadNum) return false;
    const gbFeatures = this.overlaysData?.good_brick_awards?.features || [];
    const gbMatch =
      (featureId &&
        gbFeatures.find((f) => {
          const p = f.properties || {};
          return p.id === featureId || p.building_id === featureId;
        })) ||
      (hcadNum &&
        gbFeatures.find((f) => {
          const p = f.properties || {};
          return p.hcad_num === hcadNum;
        })) ||
      null;

    const targetBuildingId = gbMatch?.properties?.building_id || featureId || "";
    const ovFeatures = this.overridesFC?.features || [];

    let renderedTileMatch = null;
    if (!this.useCanvasFallback && this.map) {
      const tileLayers = [
        ...(this.buildingFillLayerIds || []),
        ...(this.buildingExtrusionLayerIds || []),
      ].filter((id) => this.map.getLayer(id));
      if (tileLayers.length > 0) {
        const rendered = this.map.queryRenderedFeatures({ layers: tileLayers });
        renderedTileMatch =
          (targetBuildingId &&
            rendered.find((f) => {
              const p = f.properties || {};
              return p.id === targetBuildingId || p.building_id === targetBuildingId;
            })) ||
          (hcadNum &&
            rendered.find((f) => {
              const p = f.properties || {};
              return p.hcad_num === hcadNum;
            })) ||
          null;
      }
    }

    const match =
      (targetBuildingId &&
        ovFeatures.find((f) => {
          const p = f.properties || {};
          return p.id === targetBuildingId || p.building_id === targetBuildingId;
        })) ||
      (targetBuildingId &&
        this.buildingsData.find((f) => {
          const p = f.properties || {};
          return p.id === targetBuildingId || p.building_id === targetBuildingId;
        })) ||
      (hcadNum &&
        ovFeatures.find((f) => {
          const p = f.properties || {};
          return p.hcad_num === hcadNum;
        })) ||
      (hcadNum &&
        this.buildingsData.find((f) => {
          const p = f.properties || {};
          return p.hcad_num === hcadNum;
        })) ||
      renderedTileMatch ||
      gbMatch;

    if (!match) {
      if (hcadNum) {
        fetchHcadLiveRecord(hcadNum)
          .then((rec) => {
            if (!rec) return;
            const synthProps = applyOverrideToProperties(
              {
                id: `hcad_${rec.hcadNum}`,
                hcad_num: rec.hcadNum,
                address: rec.address || `HCAD ${rec.hcadNum}`,
                year_built: rec.yearImpr || 0,
                decade: rec.yearImpr ? Math.floor(rec.yearImpr / 10) * 10 : 0,
                bld_sqft: rec.bldgSqft || 0,
                land_sqft: rec.lotAreaSqft || 0,
                owner: rec.owner || "",
                subdivision: rec.subdivision || "",
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
            if (flyTo && Array.isArray(rec.centroid)) {
              this.flyToLocation({
                lng: rec.centroid[0],
                lat: rec.centroid[1],
                zoom: 17.2,
              });
            }
            this.highlightAndInspectFeature(synthProps, rec.geometry || null);
            if (!this.useCanvasFallback && this.map) {
              this.map.once("idle", () => {
                const tileLayers = [
                  ...(this.buildingFillLayerIds || []),
                  ...(this.buildingExtrusionLayerIds || []),
                ].filter((id) => this.map.getLayer(id));
                if (!tileLayers.length) return;
                const renderedAfterFly = this.map.queryRenderedFeatures({ layers: tileLayers });
                const tileBld = renderedAfterFly.find(
                  (f) => f.properties && f.properties.hcad_num === rec.hcadNum
                );
                if (tileBld) {
                  this.highlightAndInspectFeature(
                    applyOverrideToProperties(tileBld.properties || {}, this.curatedOverrides),
                    tileBld.geometry || rec.geometry || null
                  );
                }
              });
            }
          })
          .catch(() => {});
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
    return true;
  }

  setTourRoute(tour = null, activeStopIndex = -1) {
    this.activeTour = tour || null;
    this.activeTourStopIndex = Number.isInteger(activeStopIndex) ? activeStopIndex : -1;

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

  flyToLocation({ lng, lat, zoom = 16.5, pitch = null, hcadNum = "", featureId = "" }) {
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

    if (featureId || hcadNum) {
      this.selectFeatureByIdOrHcad({ hcadNum, featureId, flyTo: false });
    }
  }

  highlightAndInspectFeature(props, clickedGeometry = null) {
    if (!props) return;
    const mergedProps = applyOverrideToProperties(props, this.curatedOverrides);
    this.selectedFeatureId = mergedProps.id || "";
    this.selectedFeatureProps = mergedProps;

    // Resolve the single building polygon geometry so clicking one building on a
    // multi-building parcel (e.g. Rice University) never highlights all buildings on that parcel.
    let singleGeom = clickedGeometry || null;
    if (!singleGeom && this.selectedFeatureId) {
      const ovMatch = (this.overridesFC?.features || []).find(
        (f) => f.properties && f.properties.id === this.selectedFeatureId
      );
      if (ovMatch && ovMatch.geometry) {
        singleGeom = ovMatch.geometry;
      }
    }
    this.selectedFeatureGeometry = singleGeom;

    if (this.useCanvasFallback) {
      this._renderCanvas2D();
    } else if (this.map) {
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
            this.map.setFilter(hlId, ["==", ["get", "id"], this.selectedFeatureId]);
          }
        }
      }
    }
    if (this.onSelectFeature) {
      this.onSelectFeature(mergedProps);
    }
  }

  clearSelection() {
    this.selectedFeatureId = null;
    this.selectedFeatureProps = null;
    this.selectedFeatureGeometry = null;
    if (this.useCanvasFallback) {
      this._renderCanvas2D();
    } else if (this.map) {
      const selSrc = this.map.getSource("selected-feature-src");
      if (selSrc) {
        selSrc.setData({ type: "FeatureCollection", features: [] });
      }
      for (const hlId of this.highlightLayerIds) {
        if (this.map.getLayer(hlId)) {
          this.map.setFilter(hlId, ["==", ["get", "id"], ""]);
        }
      }
    }
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

    // In WebGL mode with countywide PMTiles shards, query rendered vector tile features first
    let usedRenderedFeatures = false;
    if (!this.useCanvasFallback && this.map) {
      const queryLayers = [
        "curated-overrides-fill",
        "curated-overrides-extrusion",
        ...this.buildingFillLayerIds,
        ...this.buildingExtrusionLayerIds,
      ].filter((id) => this.map.getLayer(id));
      if (queryLayers.length > 0) {
        const rendered = this.map.queryRenderedFeatures({ layers: queryLayers });
        if (rendered.length > 0) {
          usedRenderedFeatures = true;
          const seenIds = new Set();
          for (const feat of rendered) {
            const p = applyOverrideToProperties(feat.properties || {}, this.curatedOverrides);
            const firstPt =
              feat.geometry?.type === "Polygon"
                ? feat.geometry.coordinates?.[0]?.[0]
                : feat.geometry?.coordinates?.[0]?.[0]?.[0];
            const coordSig =
              Array.isArray(firstPt) && firstPt.length >= 2
                ? `${Number(firstPt[0]).toFixed(5)},${Number(firstPt[1]).toFixed(5)}`
                : "";
            const baseKey = p.id || p.hcad_num || "";
            const key = coordSig ? `${baseKey}:${coordSig}` : baseKey;
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
        if (!geom || !geom.coordinates) continue;

        const ring =
          geom.type === "Polygon"
            ? geom.coordinates[0]
            : geom.type === "MultiPolygon" && geom.coordinates[0]
            ? geom.coordinates[0][0]
            : null;
        if (!ring || !ring.length) continue;

        const [lon, lat] = ring[0];
        if (lon < west || lon > east || lat < south || lat > north) continue;

        const p = feat.properties || {};
        const fid = p.id || p.building_id || "";
        if (fid) {
          if (seenFallbackIds.has(fid)) continue;
          seenFallbackIds.add(fid);
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

  getRenderedBuildingCandidates(limit = 600) {
    const out = [];
    const seenKeys = new Set();

    const extractCentroid = (geom) => {
      if (!geom || !geom.coordinates) return null;
      if (geom.type === "Point" && Array.isArray(geom.coordinates)) {
        return [Number(geom.coordinates[0]), Number(geom.coordinates[1])];
      }
      const ring =
        geom.type === "Polygon"
          ? geom.coordinates[0]
          : geom.type === "MultiPolygon" && geom.coordinates[0]
          ? geom.coordinates[0][0]
          : null;
      if (!Array.isArray(ring) || !ring.length) return null;
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
      return n > 0 ? [sx / n, sy / n] : null;
    };

    if (!this.useCanvasFallback && this.map) {
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
          const key = p.id || p.building_id || p.hcad_num || "";
          if (key) {
            if (seenKeys.has(key)) continue;
            seenKeys.add(key);
          }
          const pt = extractCentroid(feat.geometry);
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

    const vp = this.getCurrentViewport ? this.getCurrentViewport() : null;
    const allFeatures = [
      ...(this.overridesFC?.features || []),
      ...(this.buildingsData || []),
    ];
    const nonViewportOut = [];
    for (const feat of allFeatures) {
      const p = applyOverrideToProperties(feat.properties || {}, this.curatedOverrides);
      if (p.suppress_only) continue;
      const key = p.id || p.building_id || p.hcad_num || "";
      if (key) {
        if (seenKeys.has(key)) continue;
        seenKeys.add(key);
      }
      const pt = extractCentroid(feat.geometry);
      if (!pt) continue;
      const inVp = Boolean(
        vp &&
          pt[0] >= vp.west &&
          pt[0] <= vp.east &&
          pt[1] >= vp.south &&
          pt[1] <= vp.north
      );
      if (inVp) {
        out.push({
          props: p,
          lon: pt[0],
          lat: pt[1],
          inViewport: true,
        });
      } else if (nonViewportOut.length < limit) {
        nonViewportOut.push({
          props: p,
          lon: pt[0],
          lat: pt[1],
          inViewport: false,
        });
      }
    }

    return out.concat(nonViewportOut).slice(0, limit * 2);
  }
}

