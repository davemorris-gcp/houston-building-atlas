/**
 * Reactive filter state store, MapLibre GL JS filter compiler, and URL hash sync
 * for The Houston Building Atlas v2.
 */

export const DEFAULT_FILTER_STATE = {
  colorMode: "year_built", // 'year_built' | 'preservation_status' | 'use_category'
  paletteStyle: "archival", // 'archival' | 'classic_ee'
  renderMode: "buildings", // 'buildings' | 'both' | 'parcels'
  basemap: "dark_archival", // 'dark_archival' | 'warm_parchment' | 'satellite'
  extrude3D: false,
  minYear: 1836,
  maxYear: 2026,
  selectedDecade: "all", // 'all' | '1830' .. '2020' | 'unknown'
  showUnknownYears: true,
  syncAnnexationToTime: false,
  isPlaying: false,
  playSpeed: 1, // 1 | 2 | 5
  layers: {
    landmarks: true,
    historicDistricts: true,
    heritageDistricts: true,
    nrhpDistricts: false,
    thcMarkers: false,
    annexations: false,
  },
};

/**
 * Compile a MapLibre GL JS filter expression for buildings / parcels given the current state.
 */
export function buildFeatureFilterExpression(state) {
  const minY = Number(state.minYear) || 1836;
  const maxY = Number(state.maxYear) || 2026;
  const dec = String(state.selectedDecade || "all");

  if (dec === "unknown") {
    return ["<", ["to-number", ["get", "year_built"], 0], 1836];
  }

  if (dec !== "all") {
    const decInt = Number(dec);
    if (!Number.isNaN(decInt) && decInt >= 1830) {
      return [
        "all",
        [">=", ["to-number", ["get", "year_built"], 0], decInt],
        ["<=", ["to-number", ["get", "year_built"], 0], decInt + 9],
      ];
    }
  }

  const datedInRange = [
    "all",
    [">=", ["to-number", ["get", "year_built"], 0], minY],
    ["<=", ["to-number", ["get", "year_built"], 0], maxY],
  ];

  if (state.showUnknownYears) {
    return [
      "any",
      ["<", ["to-number", ["get", "year_built"], 0], 1836],
      datedInRange,
    ];
  }

  return datedInRange;
}

/**
 * Evaluate whether a plain JS feature properties object passes the current filter state
 * (used for client-side stats/histogram calculation).
 */
export function featureMatchesFilter(props, state) {
  if (!props) return false;
  const yr = Number(props.year_built) || 0;
  const dec = String(state.selectedDecade || "all");

  if (dec === "unknown") {
    return yr < 1836;
  }
  if (dec !== "all") {
    const decInt = Number(dec);
    return yr >= decInt && yr <= decInt + 9;
  }
  if (yr < 1836) {
    return Boolean(state.showUnknownYears);
  }
  return yr >= state.minYear && yr <= state.maxYear;
}

/**
 * Compile a MapLibre filter expression for the Annexation History layer.
 * When `syncAnnexationToTime` is enabled or time-lapse is playing, only show annexations up to `maxYear`.
 */
export function buildAnnexationFilterExpression(state) {
  if (state.syncAnnexationToTime || state.isPlaying) {
    const cutoff = Number(state.maxYear) || 2026;
    return ["<=", ["to-number", ["get", "decade"], 1836], cutoff];
  }
  return ["all"];
}

/**
 * Serialize map viewport & filter state into a compact URL hash string.
 */
export function serializeStateToHash(state, viewport = null) {
  const params = new URLSearchParams();
  if (viewport) {
    if (typeof viewport.lat === "number") params.set("lat", viewport.lat.toFixed(5));
    if (typeof viewport.lng === "number") params.set("lng", viewport.lng.toFixed(5));
    if (typeof viewport.zoom === "number") params.set("z", viewport.zoom.toFixed(2));
    if (typeof viewport.pitch === "number" && viewport.pitch > 0) {
      params.set("pitch", Math.round(viewport.pitch).toString());
    }
  }
  if (state.colorMode !== "year_built") params.set("color", state.colorMode);
  if (state.paletteStyle !== "archival") params.set("pal", state.paletteStyle);
  if (state.renderMode !== "buildings") params.set("geom", state.renderMode);
  if (state.basemap !== "dark_archival") params.set("base", state.basemap);
  if (state.extrude3D) params.set("3d", "1");
  if (state.minYear !== 1836) params.set("minY", String(state.minYear));
  if (state.maxYear !== 2026) params.set("maxY", String(state.maxYear));
  if (state.selectedDecade !== "all") params.set("dec", String(state.selectedDecade));
  if (!state.showUnknownYears) params.set("unk", "0");
  return params.toString();
}

/**
 * Parse URL hash parameters into partial filter state + optional viewport.
 */
export function parseHashToState(hashString) {
  const clean = (hashString || "").replace(/^#/, "");
  const params = new URLSearchParams(clean);
  const patch = {};
  let viewport = null;

  const lat = parseFloat(params.get("lat") || "");
  const lng = parseFloat(params.get("lng") || "");
  const z = parseFloat(params.get("z") || "");
  const pitch = parseFloat(params.get("pitch") || "0");
  if (!Number.isNaN(lat) && !Number.isNaN(lng)) {
    viewport = {
      lat,
      lng,
      zoom: !Number.isNaN(z) ? z : 15.0,
      pitch: !Number.isNaN(pitch) ? pitch : 0,
    };
  }

  if (params.has("color")) patch.colorMode = params.get("color");
  if (params.has("pal")) patch.paletteStyle = params.get("pal");
  if (params.has("geom")) patch.renderMode = params.get("geom");
  if (params.has("base")) patch.basemap = params.get("base");
  if (params.get("3d") === "1") patch.extrude3D = true;
  if (params.has("minY")) patch.minYear = Math.max(1836, Math.min(2026, parseInt(params.get("minY"), 10) || 1836));
  if (params.has("maxY")) patch.maxYear = Math.max(1836, Math.min(2026, parseInt(params.get("maxY"), 10) || 2026));
  if (params.has("dec")) patch.selectedDecade = params.get("dec");
  if (params.get("unk") === "0") patch.showUnknownYears = false;

  return { patch, viewport };
}

/**
 * Create a reactive FilterStore instance.
 */
export function createFilterStore(initialOverrides = {}) {
  let state = {
    ...DEFAULT_FILTER_STATE,
    ...initialOverrides,
    layers: {
      ...DEFAULT_FILTER_STATE.layers,
      ...(initialOverrides.layers || {}),
    },
  };
  const listeners = new Set();

  function getState() {
    return state;
  }

  function setState(partial) {
    const nextLayers = partial.layers ? { ...state.layers, ...partial.layers } : state.layers;
    state = {
      ...state,
      ...partial,
      layers: nextLayers,
    };
    if (state.minYear > state.maxYear) {
      const tmp = state.minYear;
      state.minYear = state.maxYear;
      state.maxYear = tmp;
    }
    for (const fn of listeners) {
      fn(state);
    }
  }

  function setLayerVisibility(layerKey, visible) {
    setState({
      layers: {
        ...state.layers,
        [layerKey]: Boolean(visible),
      },
    });
  }

  function resetFilters() {
    setState({
      minYear: 1836,
      maxYear: 2026,
      selectedDecade: "all",
      showUnknownYears: true,
      isPlaying: false,
    });
  }

  function subscribe(fn) {
    listeners.add(fn);
    return () => listeners.delete(fn);
  }

  return {
    getState,
    setState,
    setLayerVisibility,
    resetFilters,
    subscribe,
  };
}
