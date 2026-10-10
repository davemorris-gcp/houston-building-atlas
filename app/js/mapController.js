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
} from "./palettes.js?v=20261010f";
import {
  buildAnnexationFilterExpression,
  buildFeatureFilterExpression,
  buildHistoricWardFilterExpression,
  featureMatchesFilter,
  resolveActiveAnnexationDecade,
  resolveActiveWardEra,
} from "./filterStore.js?v=20261010f";
import {
  applyOverrideToProperties,
  loadCuratedOverrides,
} from "./curatedEdits.js?v=20261010f";
import { fetchHcadLiveRecord } from "./hcadLink.js?v=20261010f";

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
    onOverlapStackChange,
    onIsolationChange,
  }) {
    this.containerId = containerId;
    this.filterStore = filterStore;
    this.onSelectFeature = onSelectFeature;
    this.onViewportStats = onViewportStats;
    this.onPitchChange = onPitchChange || null;
    this.onSelectTourStop = onSelectTourStop || null;
    this.onOverlapStackChange = onOverlapStackChange || null;
    this.onIsolationChange = onIsolationChange || null;
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
    const [buildingsRes, parcelsRes, overlaysRes, manifestRes, overridesResult] =
      await Promise.all([
        fetch("public/data/buildings.geojson?v=20261010f"),
        fetch("public/data/parcels.geojson?v=20261010f"),
        fetch("public/data/overlays.json?v=20261010f"),
        fetch("public/data/pmtiles_manifest.json?v=20261010f").catch(() => null),
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
    this._buildBoundarySpatialIndex();

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

    const state = this.filterStore.getState();
    const colorExpr = buildColorExpression(state.colorMode, state.paletteStyle);
    const filterExpr = buildFeatureFilterExpression(state);
    const shardFilterExpr = this._buildShardLayerFilter(filterExpr);
    const wardFilterExpr = buildHistoricWardFilterExpression(state);
    const annexFilterExpr = buildAnnexationFilterExpression(state);

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

      // 3b. Historic Wards (1839-1905), COH Super Neighborhoods (88), and Neighborhoods (1,387)
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
        if (isoGeom && !isPolygonInIsolated(feat.geometry)) continue;
        const color = evaluateFeatureColor(p, state.colorMode, state.paletteStyle);
        const isSelected =
          this.selectedFeatureId &&
          (p.id === this.selectedFeatureId || p.building_id === this.selectedFeatureId);
        const stroke = isSelected ? "#FDE047" : "rgba(15, 17, 21, 0.72)";
        const lw = isSelected ? 2.8 : 0.75;
        const extrudeM = state.extrude3D ? Number(p.height_m) || 5.0 : 0;

        const bbox = drawPolygonFeature(feat.geometry, color, stroke, lw, null, extrudeM);
        if (bbox) {
          cs.renderedBBoxes.push({ ...bbox, props: p, geom: feat.geometry });
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

    // 7. COH Designated Landmarks (LM & PLM)
    if (allowPointMarkers && state.layers.landmarks && this.overlaysData?.landmarks) {
      for (const feat of this.overlaysData.landmarks.features || []) {
        const coords = feat.geometry?.coordinates;
        if (!coords) continue;
        if (isoGeom && !isPointInIsolated(coords[0], coords[1])) continue;
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
        const name = hasDocs ? `📜 ${rawName}` : rawName;
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
  _buildTooltipHTML(p, overlapStack = []) {
    const isBoundary =
      p.overlay_layer === "neighborhoods" ||
      p.overlay_layer === "platted_subdivisions" ||
      p.overlay_layer === "land_use_protections" ||
      p.overlay_layer === "super_neighborhoods" ||
      p.overlay_layer === "historic_wards" ||
      p.overlay_layer === "historic_districts" ||
      p.overlay_layer === "heritage_districts" ||
      p.overlay_layer === "nrhp_districts" ||
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
      if (p.overlay_layer === "platted_subdivisions") {
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
      }
      const statParts = [];
      if (p.overlay_layer === "historical_waterways") {
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
            p.neighborhood && p.overlay_layer === "platted_subdivisions"
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
      badge = p.designation;
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
        state.layers?.historicMap ? "visible" : "none"
      );
      const histOpacity = Math.max(
        0.1,
        Math.min(1, (Number(state.historicMapOpacity) || 75) / 100)
      );
      this.map.setPaintProperty("basemap-historic-topo-layer", "raster-opacity", histOpacity);
    }

    const colorExpr = buildColorExpression(state.colorMode, state.paletteStyle);
    const baseFilterExpr = buildFeatureFilterExpression(state);
    const filterExpr = this._buildTourFocusFilterExpression(baseFilterExpr);
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
      [...this.buildingLineLayerIds, "curated-overrides-line"],
      showBuildings && !state.extrude3D
    );
    setVis(
      [...this.buildingExtrusionLayerIds, "curated-overrides-extrusion"],
      showBuildings && state.extrude3D
    );
    setVis(
      ["selected-feature-outline", ...(this.highlightLayerIds || [])],
      showBuildings && !state.extrude3D
    );
    setVis(["selected-feature-3d-extrusion"], showBuildings && state.extrude3D);
    setVis(["parcels-fill"], showParcelsFill);
    setVis(["parcels-line"], showParcelsLine);

    setVis(
      ["good-brick-glow", "good-brick-circle"],
      allowIsoMarkers && Boolean(state.layers.goodBrickAwards)
    );
    setVis(["landmarks-circle"], allowIsoMarkers && Boolean(state.layers.landmarks));
    setVis(["thc-markers-circle"], allowIsoMarkers && Boolean(state.layers.thcMarkers));

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
      const thcNums = (this.overlaysData?.thc_markers?.features || [])
        .filter((f) => inIso(f.geometry?.coordinates))
        .map((f) => String(f.properties?.marker_num || f.properties?.id || ""));

      const gbFilter = ["in", ["coalesce", ["get", "id"], ["get", "hcad_num"], ""], ["literal", gbIds]];
      const lmFilter = ["in", ["coalesce", ["get", "id"], ["get", "hcad_num"], ""], ["literal", lmIds]];
      const thcFilter = [
        "in",
        ["to-string", ["coalesce", ["get", "marker_num"], ["get", "id"], ""]],
        ["literal", thcNums],
      ];
      if (this.map.getLayer("good-brick-glow")) this.map.setFilter("good-brick-glow", gbFilter);
      if (this.map.getLayer("good-brick-circle")) this.map.setFilter("good-brick-circle", gbFilter);
      if (this.map.getLayer("landmarks-circle")) this.map.setFilter("landmarks-circle", lmFilter);
      if (this.map.getLayer("thc-markers-circle")) this.map.setFilter("thc-markers-circle", thcFilter);
    } else if (!isoGeom) {
      if (this.map.getLayer("good-brick-glow")) this.map.setFilter("good-brick-glow", null);
      if (this.map.getLayer("good-brick-circle")) this.map.setFilter("good-brick-circle", null);
      if (this.map.getLayer("landmarks-circle")) this.map.setFilter("landmarks-circle", null);
      if (this.map.getLayer("thc-markers-circle")) this.map.setFilter("thc-markers-circle", null);
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
        historic_districts: Boolean(state.layers?.historicDistricts),
        heritage_districts: Boolean(state.layers?.heritageDistricts),
        nrhp_districts: Boolean(state.layers?.nrhpDistricts),
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

  flyToLocation(arg1, arg2, arg3 = 16.5) {
    let lng;
    let lat;
    let zoom = 16.5;
    let pitch = null;
    let hcadNum = "";
    let featureId = "";
    if (arg1 && typeof arg1 === "object") {
      lng = Number(arg1.lng);
      lat = Number(arg1.lat);
      if (Number.isFinite(Number(arg1.zoom))) zoom = Number(arg1.zoom);
      if (arg1.pitch !== undefined && arg1.pitch !== null) pitch = Number(arg1.pitch);
      hcadNum = arg1.hcadNum || "";
      featureId = arg1.featureId || "";
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

    if (featureId || hcadNum) {
      this.selectFeatureByIdOrHcad({ hcadNum, featureId, flyTo: false });
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

    const fromBuildings = searchCollection(this.buildingsData);
    if (fromBuildings) return fromBuildings;

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
    if (!state.extrude3D || !this.selectedFeatureProps || !this.selectedFeatureGeometry) {
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
    indexLayer(this.overlaysData?.platted_subdivisions, "platted_subdivisions");
    indexLayer(this.overlaysData?.land_use_protections, "land_use_protections");
    indexLayer(this.overlaysData?.neighborhoods, "neighborhoods");
    indexLayer(this.overlaysData?.super_neighborhoods, "super_neighborhoods");
    indexLayer(this.overlaysData?.historic_wards, "historic_wards");
    indexLayer(this.overlaysData?.historic_districts, "historic_districts");
    indexLayer(this.overlaysData?.heritage_districts, "heritage_districts");
    indexLayer(this.overlaysData?.nrhp_districts, "nrhp_districts");
    indexLayer(this.overlaysData?.annexations, "annexations");
    indexLayer(this.overlaysData?.historical_waterways, "historical_waterways", true);
    indexLayer(this.overlaysData?.historical_railroads, "historical_railroads", true);
  }

  _normalizeBoundaryOverlayKey(rawKey) {
    const k = String(rawKey || "").trim();
    const map = {
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
        "neighborhoods-fill",
        "super-neighborhoods-fill",
        "historic-wards-fill",
        "historic-districts-fill",
        "heritage-districts-fill",
        "nrhp-districts-fill",
        "annexations-fill",
      ]);
      const nonBoundaryLayers = [
        "good-brick-circle",
        "landmarks-circle",
        "thc-markers-circle",
        "curated-overrides-extrusion",
        "curated-overrides-fill",
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

        if ((lid === "landmarks-circle" || lid === "good-brick-circle") && (p.hcad_num || p.building_id)) {
          const bldHit =
            (p.building_id &&
              (this.overridesFC?.features || []).find(
                (bf) =>
                  bf.properties &&
                  (bf.properties.building_id === p.building_id || bf.properties.id === p.building_id)
              )) ||
            rendered.find(
              (bf) =>
                bf.layer?.id !== "landmarks-circle" &&
                bf.layer?.id !== "good-brick-circle" &&
                bf.layer?.id !== "thc-markers-circle"
            ) ||
            (p.hcad_num &&
              (this.overridesFC?.features || []).find(
                (bf) => bf.properties && bf.properties.hcad_num === p.hcad_num
              )) ||
            (p.hcad_num &&
              this.buildingsData.find(
                (bf) => bf.properties && bf.properties.hcad_num === p.hcad_num
              ));
          if (bldHit) {
            const baseBldProps = applyOverrideToProperties(
              bldHit.properties || {},
              this.curatedOverrides
            );
            const mergedProps = {
              ...baseBldProps,
              landmark_name: p.landmark_name || baseBldProps.landmark_name || p.name || "",
              good_brick_awards: p.good_brick_awards || baseBldProps.good_brick_awards || null,
              good_brick_summary: p.good_brick_summary || baseBldProps.good_brick_summary || "",
              good_brick_years: p.good_brick_years || baseBldProps.good_brick_years || "",
            };
            const bldKey = `bld:${
              mergedProps.building_id || mergedProps.id || mergedProps.hcad_num || makeBuildingTitle(mergedProps)
            }`;
            if (!seenKeys.has(bldKey)) {
              seenKeys.add(bldKey);
              if (mergedProps.hcad_num) seenKeys.add(`hcad:${mergedProps.hcad_num}`);
              const bTypeBadge =
                lid === "good-brick-circle"
                  ? "Good Brick"
                  : lid === "landmarks-circle"
                  ? "Landmark"
                  : "Building";
              const bSwatchColor = lid === "good-brick-circle" ? "#95c959" : "#fde047";
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
        const bBadge = lid === "parcels-fill" ? "Tax Parcel" : "Building";
        const bColor = lid === "parcels-fill" ? "#94a3b8" : "#fde047";
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
        neighborhoods: {
          enabled: Boolean(state.layers?.neighborhoods),
          priority: 40,
          typeBadge: "Neighborhood",
          swatchColor: "#38bdf8",
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
        if (entry.overlayKey === "historic_wards") {
          const entryEra = Number(
            entry.props.era || entry.props.ward_era || entry.props.era_year || 0
          );
          if (entryEra !== activeWardEra) continue;
        } else if (entry.overlayKey === "annexations") {
          const annexDecade = Number(entry.props.decade || entry.props.year || 1836);
          if (annexDecade !== activeAnnexDecade) continue;
          if (!showSpokes && entry.props.annex_subtype === "spoke_or_spa") continue;
        }
        const [minLng, minLat, maxLng, maxLat] = entry.bbox;
        if (lng < minLng || lng > maxLng || lat < minLat || lat > maxLat) continue;
        if (!this._pointInPolygonGeometry(lng, lat, entry.feature.geometry)) continue;
        let dynBadge = meta.typeBadge;
        let dynSwatch = entry.props.color || meta.swatchColor;
        if (entry.overlayKey === "platted_subdivisions" && entry.props.has_deed_docs) {
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
        stack.push({
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
            Number(ep.building_count) > 0
              ? `${Number(ep.building_count).toLocaleString()} structures`
              : ep.type || mb.typeBadge,
          props: {
            ...ep,
            overlay_layer: mb.entry.overlayKey,
            is_boundary_feature: true,
          },
          geometry: mb.entry.feature.geometry,
        });
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
        flyTo: false,
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
        if (this.onSelectFeature) {
          this.onSelectFeature(feat.properties);
        }
      }
    } else {
      this.clearHighlightedBoundary();
      this.highlightAndInspectFeature(item.props, item.geometry || null, {
        keepOverlapStack: true,
      });
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

  highlightBoundaryByIdOrName(arg1 = {}, arg2 = {}) {
    let opts = {};
    if (typeof arg1 === "string") {
      opts = {
        ...arg2,
        id: arg1,
        name: arg1,
      };
    } else if (arg1 && typeof arg1 === "object") {
      opts = arg1;
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
      const isSmallBoundary =
        matchEntry.overlayKey === "platted_subdivisions" ||
        matchEntry.overlayKey === "neighborhoods";
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
              maxZoom:
                matchEntry.overlayKey === "platted_subdivisions"
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

    if (inspect && this.onSelectFeature) {
      this.onSelectFeature({
        ...feat.properties,
        lng: matchEntry.centroid[0],
        lat: matchEntry.centroid[1],
      });
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
        }
      }
    }

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
      this._updateSelectedFeature3DSource();
    }
    if (this.onSelectFeature) {
      this.onSelectFeature(mergedProps);
    }
  }

  clearSelection(opts = {}) {
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
        .replace(/\s+(protected\s+)?(historic|heritage|nrhp)\s+district$/i, "")
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
        matchEntry.overlayKey === "heritage_districts";
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

