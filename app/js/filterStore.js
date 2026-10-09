/**
 * Reactive filter state store, MapLibre GL JS filter compiler, and URL hash sync
 * for The Houston Building Atlas v2.
 */

export const DEFAULT_FILTER_STATE = {
  colorMode: "year_built", // 'year_built' | 'preservation_status' | 'use_category'
  paletteStyle: "archival", // 'archival' | 'classic_ee'
  renderMode: "buildings", // 'buildings' | 'both' | 'parcels' | 'none'
  lastActiveRenderMode: "buildings", // remembers 'buildings' | 'both' | 'parcels' when buildings are toggled off
  basemap: "dark_archival", // 'dark_archival' | 'warm_parchment' | 'satellite'
  extrude3D: false,
  minYear: 1836,
  maxYear: 2026,
  selectedDecade: "all", // 'all' | '1830' .. '2020' | 'unknown'
  showUnknownYears: true,
  syncAnnexationToTime: false,
  isPlaying: false,
  playSpeed: 1, // 1 | 2 | 5
  stepYears: 5, // 1 | 5 | 10
  singleLayerMode: false, // when true, clicking any overlay activates only that single overlay
  preSoloLayers: null, // snapshot of overlay visibility before Solo was clicked
  historicMapOpacity: 75, // 15..100 opacity percentage for the Historic Topo Map overlay
  wardEra: 1920, // 1839 | 1866 | 1896 | 1903 | 1920
  showAnnexationSpokes: true, // include 1963 10-ft highway ETJ spokes & 2000s-2010s MUD SPA commercial strips
  layers: {
    goodBrickAwards: true,
    landmarks: true,
    historicDistricts: true,
    heritageDistricts: true,
    nrhpDistricts: false,
    thcMarkers: false,
    neighborhoods: false,
    plattedSubdivisions: false,
    superNeighborhoods: false,
    historicWards: false,
    annexations: false,
    historicMap: false,
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
      const lowBound = decInt === 1840 ? 1836 : decInt;
      return [
        "all",
        [">=", ["to-number", ["get", "year_built"], 0], lowBound],
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
    const lowBound = decInt === 1840 ? 1836 : decInt;
    return yr >= lowBound && yr <= decInt + 9;
  }
  if (yr < 1836) {
    return Boolean(state.showUnknownYears);
  }
  return yr >= state.minYear && yr <= state.maxYear;
}

export const ANNEXATION_MILESTONE_DECADES = [
  1836, 1840, 1900, 1910, 1920, 1930, 1940, 1950, 1960, 1970, 1980, 1990, 2000, 2010, 2020,
];

export const HISTORIC_WARD_ERAS = [1839, 1866, 1896, 1903, 1920];

/**
 * Resolve the active Historic Ward boundary era (1839, 1866, 1896, 1903, or 1920).
 * When time-lapse playback (`isPlaying`) is active, steps through the
 * historical ward charters in lockstep with the timeline; otherwise uses `state.wardEra`.
 */
export function resolveActiveWardEra(state) {
  if (!state) return 1920;
  const decStr = String(state.selectedDecade || "all");
  const maxY = Number(state.maxYear) || 2026;
  if (Boolean(state.isPlaying)) {
    let cutoff = maxY;
    if (decStr !== "all" && decStr !== "unknown") {
      const decInt = Number(decStr);
      if (!Number.isNaN(decInt) && decInt >= 1830) {
        cutoff = decInt === 1836 ? 1839 : decInt + 9;
      }
    }
    let matched = 1839;
    for (const era of HISTORIC_WARD_ERAS) {
      if (era <= cutoff) {
        matched = era;
      } else {
        break;
      }
    }
    return matched;
  }
  const explicitEra = Number(state.wardEra) || 1920;
  return HISTORIC_WARD_ERAS.includes(explicitEra) ? explicitEra : 1920;
}

/**
 * Compile a MapLibre filter expression for the Historic Wards layer.
 */
export function buildHistoricWardFilterExpression(state) {
  const era = resolveActiveWardEra(state);
  return ["==", ["to-number", ["get", "era"], 1920], era];
}

/**
 * Resolve the single cumulative annexation boundary decade for a given filter state.
 * Always resolves to one clean cumulative milestone decade (defaulting to 2020 when no
 * earlier era/year filter is active) rather than stacking all 15 historical polygons at once.
 */
export function resolveActiveAnnexationDecade(state) {
  if (!state) return 2020;
  const decStr = String(state.selectedDecade || "all");
  const maxY = Number(state.maxYear) || 2026;

  let cutoff = maxY;
  if (decStr !== "all" && decStr !== "unknown") {
    const decInt = Number(decStr);
    if (!Number.isNaN(decInt) && decInt >= 1830) {
      cutoff = decInt === 1836 ? 1836 : decInt + 9;
    }
  }

  let matched = 1836;
  for (const milestone of ANNEXATION_MILESTONE_DECADES) {
    if (milestone <= cutoff) {
      matched = milestone;
    } else {
      break;
    }
  }
  return matched;
}

/**
 * Compile a MapLibre filter expression for the Annexation History layer.
 * Renders the single clean dissolved cumulative city boundary for the active era,
 * optionally excluding 1963 10-ft highway spokes & 2000s-2010s MUD SPA commercial strips.
 */
export function buildAnnexationFilterExpression(state) {
  const activeDecade = resolveActiveAnnexationDecade(state) || 2020;
  const decClause = ["==", ["to-number", ["get", "decade"], 1836], activeDecade];
  if (state && state.showAnnexationSpokes === false) {
    return [
      "all",
      decClause,
      ["==", ["coalesce", ["get", "annex_subtype"], "full_purpose"], "full_purpose"],
    ];
  }
  return decClause;
}

function layersMatchDefault(layers) {
  if (!layers) return true;
  const def = DEFAULT_FILTER_STATE.layers;
  return Object.keys(def).every((k) => Boolean(layers[k]) === Boolean(def[k]));
}

const LAYER_KEY_TO_SHORT = {
  goodBrickAwards: "good_brick",
  landmarks: "landmarks",
  historicDistricts: "historic_districts",
  heritageDistricts: "heritage_districts",
  nrhpDistricts: "nrhp_districts",
  thcMarkers: "thc_markers",
  neighborhoods: "neighborhoods",
  plattedSubdivisions: "platted_subdivisions",
  superNeighborhoods: "super_neighborhoods",
  historicWards: "historic_wards",
  annexations: "annexations",
  historicMap: "historic_map",
};

const SHORT_TO_LAYER_KEY = {
  goodbrickawards: "goodBrickAwards",
  good_brick_awards: "goodBrickAwards",
  good_brick: "goodBrickAwards",
  goodbrick: "goodBrickAwards",
  awards: "goodBrickAwards",
  gba: "goodBrickAwards",
  landmarks: "landmarks",
  landmark: "landmarks",
  lm: "landmarks",
  historicdistricts: "historicDistricts",
  historic_districts: "historicDistricts",
  districts: "historicDistricts",
  hd: "historicDistricts",
  heritagedistricts: "heritageDistricts",
  heritage_districts: "heritageDistricts",
  heritage: "heritageDistricts",
  nrhpdistricts: "nrhpDistricts",
  nrhp_districts: "nrhpDistricts",
  nrhp: "nrhpDistricts",
  thcmarkers: "thcMarkers",
  thc_markers: "thcMarkers",
  thc: "thcMarkers",
  markers: "thcMarkers",
  neighborhoods: "neighborhoods",
  neighborhood: "neighborhoods",
  nbhd: "neighborhoods",
  plattedsubdivisions: "plattedSubdivisions",
  platted_subdivisions: "plattedSubdivisions",
  subdivisions: "plattedSubdivisions",
  subdivision: "plattedSubdivisions",
  plats: "plattedSubdivisions",
  plat: "plattedSubdivisions",
  superneighborhoods: "superNeighborhoods",
  super_neighborhoods: "superNeighborhoods",
  super_neighborhood: "superNeighborhoods",
  snbr: "superNeighborhoods",
  historicwards: "historicWards",
  historic_wards: "historicWards",
  wards: "historicWards",
  ward: "historicWards",
  annexations: "annexations",
  annexation: "annexations",
  annex: "annexations",
  historicmap: "historicMap",
  historic_map: "historicMap",
  topo: "historicMap",
  usgs: "historicMap",
};

const BASEMAP_TO_SHORT = {
  dark_archival: "dark",
  warm_parchment: "light",
  satellite: "satellite",
};

const SHORT_TO_BASEMAP = {
  dark_archival: "dark_archival",
  dark: "dark_archival",
  archival: "dark_archival",
  warm_parchment: "warm_parchment",
  light: "warm_parchment",
  parchment: "warm_parchment",
  warm: "warm_parchment",
  satellite: "satellite",
  sat: "satellite",
  aerial: "satellite",
  imagery: "satellite",
};

const SHORT_TO_RENDER_MODE = {
  buildings: "buildings",
  footprints: "buildings",
  on: "buildings",
  "1": "buildings",
  true: "buildings",
  both: "both",
  "buildings+parcels": "both",
  parcels: "parcels",
  lots: "parcels",
  none: "none",
  off: "none",
  "0": "none",
  false: "none",
  hidden: "none",
};

const SHORT_TO_COLOR_MODE = {
  year_built: "year_built",
  age: "year_built",
  year: "year_built",
  preservation_status: "preservation_status",
  status: "preservation_status",
  preservation: "preservation_status",
  use_category: "use_category",
  use: "use_category",
  landuse: "use_category",
};

export const SHARE_VIEW_PRESETS = [
  {
    id: "good_brick_light",
    label: "Good Brick Award Winners Only (Light Map)",
    description: "Warm Parchment basemap with building footprints turned off and Good Brick Award winners soloed.",
    statePatch: {
      basemap: "warm_parchment",
      renderMode: "none",
      extrude3D: false,
      minYear: 1836,
      maxYear: 2026,
      selectedDecade: "all",
      layers: {
        goodBrickAwards: true,
        landmarks: false,
        historicDistricts: false,
        heritageDistricts: false,
        nrhpDistricts: false,
        thcMarkers: false,
        annexations: false,
      },
    },
    viewport: { lat: 29.7585, lng: -95.3785, zoom: 12.6, pitch: 0 },
  },
  {
    id: "good_brick_districts_dark",
    label: "Good Brick Winners + Historic Districts (Dark Map)",
    description: "Archival Dark basemap showing Good Brick Award recipients in context with City Historic Districts.",
    statePatch: {
      basemap: "dark_archival",
      renderMode: "none",
      extrude3D: false,
      minYear: 1836,
      maxYear: 2026,
      selectedDecade: "all",
      layers: {
        goodBrickAwards: true,
        landmarks: false,
        historicDistricts: true,
        heritageDistricts: false,
        nrhpDistricts: false,
        thcMarkers: false,
        annexations: false,
      },
    },
    viewport: { lat: 29.7662, lng: -95.3805, zoom: 12.8, pitch: 0 },
  },
  {
    id: "landmarks_districts_light",
    label: "Designated Landmarks & Historic Districts (Light Map)",
    description: "Clean Parchment map highlighting Protected Landmarks, Landmarks, and Historic Districts without footprints.",
    statePatch: {
      basemap: "warm_parchment",
      renderMode: "none",
      extrude3D: false,
      minYear: 1836,
      maxYear: 2026,
      selectedDecade: "all",
      layers: {
        goodBrickAwards: false,
        landmarks: true,
        historicDistricts: true,
        heritageDistricts: true,
        nrhpDistricts: true,
        thcMarkers: false,
        annexations: false,
      },
    },
    viewport: { lat: 29.7662, lng: -95.3805, zoom: 13.0, pitch: 0 },
  },
  {
    id: "pre1940_buildings_only",
    label: "Pre-1940 Historic Structures Only",
    description: "1836–1939 structures across Houston with all overlay pins hidden for pure architectural footprint clarity.",
    statePatch: {
      basemap: "dark_archival",
      renderMode: "buildings",
      colorMode: "year_built",
      extrude3D: false,
      minYear: 1836,
      maxYear: 1939,
      selectedDecade: "all",
      layers: {
        goodBrickAwards: false,
        landmarks: false,
        historicDistricts: false,
        heritageDistricts: false,
        nrhpDistricts: false,
        thcMarkers: false,
        annexations: false,
      },
    },
    viewport: { lat: 29.7662, lng: -95.3805, zoom: 13.6, pitch: 0 },
  },
  {
    id: "full_atlas_default",
    label: "Full Building Atlas (Default View)",
    description: "All 1.51M dated building footprints + Good Brick Awards, Landmarks, and Historic Districts.",
    statePatch: {
      basemap: "dark_archival",
      renderMode: "buildings",
      colorMode: "year_built",
      extrude3D: false,
      minYear: 1836,
      maxYear: 2026,
      selectedDecade: "all",
      layers: { ...DEFAULT_FILTER_STATE.layers },
    },
    viewport: { lat: 29.7662, lng: -95.3805, zoom: 15.2, pitch: 0 },
  },
];

/**
 * Serialize map viewport, filter state, and optional selected feature into URL parameters.
 * Supports both compact hash strings and clean human-readable query URLs.
 */
export function serializeStateToHash(state, viewport = null, options = {}) {
  const {
    includeViewport = true,
    selectedHcad = "",
    selectedId = "",
    selectedFeatureId = "",
    collapseSidebar = false,
    humanReadable = false,
  } = options;
  const effectiveId = selectedFeatureId || selectedId;
  const params = new URLSearchParams();

  if (humanReadable) {
    params.set("base", BASEMAP_TO_SHORT[state.basemap] || state.basemap);
    params.set("buildings", state.renderMode === "none" ? "off" : state.renderMode);
    if (state.layers) {
      const activeShorts = Object.keys(DEFAULT_FILTER_STATE.layers)
        .filter((k) => state.layers[k])
        .map((k) => LAYER_KEY_TO_SHORT[k] || k);
      params.set("layers", activeShorts.length ? activeShorts.join(",") : "none");
    }
  } else {
    if (state.basemap !== "dark_archival") {
      params.set("base", BASEMAP_TO_SHORT[state.basemap] || state.basemap);
    }
    if (state.renderMode !== "buildings") {
      params.set("buildings", state.renderMode === "none" ? "off" : state.renderMode);
    }
    if (state.layers && !layersMatchDefault(state.layers)) {
      const activeShorts = Object.keys(DEFAULT_FILTER_STATE.layers)
        .filter((k) => state.layers[k])
        .map((k) => LAYER_KEY_TO_SHORT[k] || k);
      params.set("layers", activeShorts.length ? activeShorts.join(",") : "none");
    }
  }

  if (state.colorMode !== "year_built") params.set("color", state.colorMode);
  if (state.paletteStyle !== "archival") params.set("pal", state.paletteStyle);
  if (state.extrude3D) params.set("3d", "1");
  if (state.minYear !== 1836) params.set("minY", String(state.minYear));
  if (state.maxYear !== 2026) params.set("maxY", String(state.maxYear));
  if (state.selectedDecade !== "all") params.set("dec", String(state.selectedDecade));
  if (!state.showUnknownYears) params.set("unk", "0");
  if (state.layers?.annexations || state.syncAnnexationToTime) params.set("syncAnnex", "1");
  if (state.showAnnexationSpokes === false) params.set("spokes", "0");
  if (state.singleLayerMode) params.set("1x", "1");
  if (state.layers?.historicMap && Number(state.historicMapOpacity) !== 75) {
    params.set("histOpacity", String(Math.round(Number(state.historicMapOpacity) || 75)));
  }
  if (state.layers?.historicWards && Number(state.wardEra) && Number(state.wardEra) !== 1920) {
    params.set("wardEra", String(Number(state.wardEra)));
  }

  if (selectedHcad) {
    params.set("hcad", String(selectedHcad).trim());
  } else if (effectiveId) {
    params.set("id", String(effectiveId).trim());
  }

  if (collapseSidebar) {
    params.set("sidebar", "0");
  }

  if (includeViewport && viewport) {
    if (typeof viewport.lat === "number") params.set("lat", viewport.lat.toFixed(5));
    if (typeof viewport.lng === "number") params.set("lng", viewport.lng.toFixed(5));
    if (typeof viewport.zoom === "number") params.set("z", viewport.zoom.toFixed(2));
    if (typeof viewport.pitch === "number" && Math.round(viewport.pitch) > 0) {
      params.set("pitch", Math.round(viewport.pitch).toString());
    }
  }

  // Keep comma-separated layer lists unescaped for clean, readable URLs
  return params.toString().replace(/%2C/gi, ",");
}

/**
 * Build a complete shareable URL from the current origin + pathname.
 */
export function buildShareableUrl(state, viewport = null, options = {}) {
  const baseUrl =
    options.baseOriginPath ||
    (typeof window !== "undefined"
      ? `${window.location.origin}${window.location.pathname}`
      : "https://davemorris-gcp.github.io/houston-building-atlas/");
  const useQuery = options.useQueryParams !== false && options.useQueryString !== false;
  const serialized = serializeStateToHash(state, viewport, {
    includeViewport: options.includeViewport !== false,
    selectedHcad: options.selectedHcad || "",
    selectedFeatureId: options.selectedFeatureId || options.selectedId || "",
    collapseSidebar: Boolean(options.collapseSidebar),
    humanReadable: true,
  });
  if (!serialized) return baseUrl;
  return useQuery ? `${baseUrl}?${serialized}` : `${baseUrl}#${serialized}`;
}

/**
 * Parse URL query string (`window.location.search`) and/or hash (`window.location.hash`)
 * into partial filter state + optional viewport + optional selected feature.
 */
export function parseHashToState(hashString = "", searchString = "") {
  const mergedParams = new URLSearchParams();
  const cleanSearch = (
    searchString || (typeof window !== "undefined" ? window.location.search : "")
  ).replace(/^\?/, "");
  const cleanHash = (hashString || "").replace(/^#/, "");

  // Apply query params first, then hash params so either (or both) work seamlessly
  for (const rawPart of [cleanSearch, cleanHash]) {
    if (!rawPart) continue;
    const sp = new URLSearchParams(rawPart);
    for (const [k, v] of sp.entries()) {
      mergedParams.set(k, v);
    }
  }

  const patch = {};
  let viewport = null;
  let selection = null;
  let collapseSidebar = null;

  // Check preset first (can be overridden by explicit params)
  if (mergedParams.has("preset")) {
    const presetId = (mergedParams.get("preset") || "").trim().toLowerCase();
    const foundPreset = SHARE_VIEW_PRESETS.find((p) => p.id.toLowerCase() === presetId);
    if (foundPreset) {
      Object.assign(patch, foundPreset.statePatch);
      if (foundPreset.statePatch.layers) {
        patch.layers = { ...foundPreset.statePatch.layers };
      }
      if (foundPreset.viewport) {
        viewport = { ...foundPreset.viewport };
      }
    }
  }

  const lat = parseFloat(mergedParams.get("lat") || "");
  const lng = parseFloat(mergedParams.get("lng") || mergedParams.get("lon") || "");
  const z = parseFloat(mergedParams.get("z") || mergedParams.get("zoom") || "");
  const pitch = parseFloat(
    mergedParams.get("pitch") || mergedParams.get("tilt") || (viewport ? String(viewport.pitch || 0) : "0")
  );
  if (!Number.isNaN(lat) && !Number.isNaN(lng)) {
    viewport = {
      lat,
      lng,
      zoom: !Number.isNaN(z) ? z : viewport?.zoom || 15.0,
      pitch: !Number.isNaN(pitch) ? pitch : 0,
    };
  }

  // Color Mode
  const rawColor = (mergedParams.get("color") || mergedParams.get("colorMode") || "").trim().toLowerCase();
  if (rawColor && SHORT_TO_COLOR_MODE[rawColor]) {
    patch.colorMode = SHORT_TO_COLOR_MODE[rawColor];
  }

  // Palette Style
  const rawPal = (mergedParams.get("pal") || mergedParams.get("palette") || "").trim().toLowerCase();
  if (rawPal) {
    patch.paletteStyle = rawPal === "classic" ? "classic_ee" : rawPal;
  }

  // Building Footprints / Render Mode (`geom` or `buildings` or `render`)
  const rawGeom = (
    mergedParams.get("buildings") ??
    mergedParams.get("geom") ??
    mergedParams.get("render") ??
    ""
  )
    .trim()
    .toLowerCase();
  if (rawGeom && SHORT_TO_RENDER_MODE[rawGeom]) {
    const g = SHORT_TO_RENDER_MODE[rawGeom];
    patch.renderMode = g;
    if (g !== "none") patch.lastActiveRenderMode = g;
  }

  // Basemap (`base` or `basemap` or `map`)
  const rawBase = (
    mergedParams.get("base") ||
    mergedParams.get("basemap") ||
    mergedParams.get("map") ||
    ""
  )
    .trim()
    .toLowerCase();
  if (rawBase && SHORT_TO_BASEMAP[rawBase]) {
    patch.basemap = SHORT_TO_BASEMAP[rawBase];
  }

  // 3D Extrusion
  if (mergedParams.has("3d")) {
    const v3d = (mergedParams.get("3d") || "").trim().toLowerCase();
    patch.extrude3D = v3d === "1" || v3d === "true" || v3d === "on" || v3d === "yes";
  }

  // Timeline / Decade
  const rawMinY = mergedParams.get("minY") || mergedParams.get("minYear");
  if (rawMinY !== null && rawMinY !== undefined && rawMinY !== "") {
    patch.minYear = Math.max(1836, Math.min(2026, parseInt(rawMinY, 10) || 1836));
  }
  const rawMaxY = mergedParams.get("maxY") || mergedParams.get("maxYear");
  if (rawMaxY !== null && rawMaxY !== undefined && rawMaxY !== "") {
    patch.maxYear = Math.max(1836, Math.min(2026, parseInt(rawMaxY, 10) || 2026));
  }
  const rawDec = mergedParams.get("dec") || mergedParams.get("decade");
  if (rawDec) {
    patch.selectedDecade = rawDec.replace(/s$/i, "");
  }
  if (mergedParams.get("unk") === "0" || mergedParams.get("unk") === "false") {
    patch.showUnknownYears = false;
  }
  if (mergedParams.get("syncAnnex") === "1" || mergedParams.get("syncAnnex") === "true") {
    patch.syncAnnexationToTime = true;
  } else if (mergedParams.get("syncAnnex") === "0" || mergedParams.get("syncAnnex") === "false") {
    patch.syncAnnexationToTime = false;
  }
  if (mergedParams.get("spokes") === "0" || mergedParams.get("spokes") === "false") {
    patch.showAnnexationSpokes = false;
  } else if (mergedParams.get("spokes") === "1" || mergedParams.get("spokes") === "true") {
    patch.showAnnexationSpokes = true;
  }
  if (mergedParams.get("1x") === "1" || mergedParams.get("singleLayer") === "1") {
    patch.singleLayerMode = true;
  }
  const rawHistOp = mergedParams.get("histOpacity") || mergedParams.get("topoOpacity");
  if (rawHistOp !== null && rawHistOp !== undefined && rawHistOp !== "") {
    patch.historicMapOpacity = Math.max(15, Math.min(100, parseInt(rawHistOp, 10) || 75));
  }
  const rawWardEra = mergedParams.get("wardEra") || mergedParams.get("ward_era");
  if (rawWardEra !== null && rawWardEra !== undefined && rawWardEra !== "") {
    const parsedEra = parseInt(rawWardEra, 10);
    if (HISTORIC_WARD_ERAS.includes(parsedEra)) {
      patch.wardEra = parsedEra;
    }
  }

  // Overlay Layers (`layers` or `ov` or `overlays`)
  const rawOv =
    mergedParams.get("layers") ?? mergedParams.get("ov") ?? mergedParams.get("overlays") ?? null;
  if (rawOv !== null) {
    const cleanOv = rawOv.trim().toLowerCase();
    const parsedLayers = {};
    if (cleanOv === "all") {
      for (const k of Object.keys(DEFAULT_FILTER_STATE.layers)) {
        parsedLayers[k] = true;
      }
    } else if (cleanOv === "none" || cleanOv === "off" || cleanOv === "0" || cleanOv === "") {
      for (const k of Object.keys(DEFAULT_FILTER_STATE.layers)) {
        parsedLayers[k] = false;
      }
    } else {
      const tokens = cleanOv
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean);
      const activeCanonical = new Set();
      for (const tok of tokens) {
        const mapped = SHORT_TO_LAYER_KEY[tok] || SHORT_TO_LAYER_KEY[tok.replace(/[^a-z0-9_]/g, "")];
        if (mapped) {
          activeCanonical.add(mapped);
        } else if (tok in DEFAULT_FILTER_STATE.layers) {
          activeCanonical.add(tok);
        }
      }
      for (const k of Object.keys(DEFAULT_FILTER_STATE.layers)) {
        parsedLayers[k] = activeCanonical.has(k);
      }
    }
    patch.layers = parsedLayers;
  }

  // Also allow individual overlay flags like `?good_brick=1` or `?landmarks=0`
  for (const [alias, canonicalKey] of Object.entries(SHORT_TO_LAYER_KEY)) {
    if (mergedParams.has(alias)) {
      const val = (mergedParams.get(alias) || "").trim().toLowerCase();
      const enabled = val === "1" || val === "true" || val === "on" || val === "yes";
      const disabled = val === "0" || val === "false" || val === "off" || val === "no";
      if (enabled || disabled) {
        if (!patch.layers) {
          patch.layers = { ...DEFAULT_FILTER_STATE.layers };
        }
        patch.layers[canonicalKey] = enabled;
      }
    }
  }

  // Keep syncAnnexationToTime and layers.annexations in lockstep when parsed from URL
  if (typeof patch.syncAnnexationToTime === "boolean") {
    if (!patch.layers) {
      patch.layers = { ...DEFAULT_FILTER_STATE.layers };
    }
    if (patch.syncAnnexationToTime) {
      patch.layers.annexations = true;
    }
  }
  if (patch.layers && typeof patch.layers.annexations === "boolean") {
    patch.syncAnnexationToTime = patch.layers.annexations;
  }

  // Optional Selected Property (`hcad` or `id` or `selected`)
  const selHcad = (mergedParams.get("hcad") || "").trim();
  const selId = (mergedParams.get("id") || mergedParams.get("selected") || "").trim();
  if (selHcad || selId) {
    selection = { hcadNum: selHcad, featureId: selId };
  }

  // Optional Sidebar collapsed state (`sidebar=0` / `embed=1`)
  const rawSidebar = (mergedParams.get("sidebar") || "").trim().toLowerCase();
  if (
    rawSidebar === "0" ||
    rawSidebar === "off" ||
    rawSidebar === "collapsed" ||
    mergedParams.get("embed") === "1"
  ) {
    collapseSidebar = true;
  } else if (rawSidebar === "1" || rawSidebar === "on" || rawSidebar === "open") {
    collapseSidebar = false;
  }

  return { patch, viewport, selection, collapseSidebar };
}

/**
 * Compute the partial state update when stepping backward (`direction = -1`)
 * or forward (`direction = 1`) by `state.stepYears` (1, 5, or 10 years).
 *
 * - If a specific decade is active (`selectedDecade !== "all"`) and `stepYears === 10`,
 *   cycles to the previous/next decade (`1840` .. `2020`).
 * - If a sliding window is active (`minYear > 1836` or a decade with `stepYears` 1 or 5),
 *   shifts both `minYear` and `maxYear` by `direction * stepYears`.
 * - Otherwise (cumulative mode, `minYear === 1836`), pauses playback and shifts
 *   `maxYear` by `direction * stepYears` (or starts at `1840 + step` if stepping forward from `2026`).
 */
export function computeStepTimeState(state, direction) {
  const step = Number(state.stepYears) || 1;
  const dir = direction < 0 ? -1 : 1;
  const delta = dir * step;
  const minY = Number(state.minYear) || 1836;
  const maxY = Number(state.maxYear) || 2026;
  const dec = String(state.selectedDecade || "all");

  // Case 1a: Decade mode with 10-year step -> cycle between decades (1840 .. 2020)
  if (dec !== "all" && dec !== "unknown" && step === 10) {
    const decInt = Number(dec);
    if (!Number.isNaN(decInt)) {
      const nextDec = Math.max(1840, Math.min(2020, decInt + delta));
      return {
        minYear: nextDec === 1840 ? 1836 : nextDec,
        maxYear: Math.min(2026, nextDec + 9),
        selectedDecade: String(nextDec),
        isPlaying: false,
      };
    }
  }

  // Case 1b: Sliding window mode (minYear > 1836 or decade mode with 1/5 yr step)
  if ((dec !== "all" && dec !== "unknown") || minY > 1836) {
    let curMin = minY;
    let curMax = maxY;
    if (dec !== "all" && dec !== "unknown") {
      const decInt = Number(dec);
      if (!Number.isNaN(decInt)) {
        curMin = decInt;
        curMax = Math.min(2026, decInt + 9);
      }
    }
    const span = Math.max(0, curMax - curMin);
    let nextMin = curMin + delta;
    let nextMax = curMax + delta;

    if (nextMin < 1836) {
      nextMin = 1836;
      nextMax = Math.min(2026, 1836 + span);
    }
    if (nextMax > 2026) {
      nextMax = 2026;
      nextMin = Math.max(1836, 2026 - span);
    }

    let nextDecade = "all";
    if (
      nextMin >= 1840 &&
      nextMin <= 2020 &&
      nextMin % 10 === 0 &&
      (nextMax === nextMin + 9 || (nextMin === 2020 && nextMax === 2026))
    ) {
      nextDecade = String(nextMin);
    }

    return {
      minYear: nextMin,
      maxYear: nextMax,
      selectedDecade: nextDecade,
      isPlaying: false,
    };
  }

  // Case 2: Cumulative growth mode (minYear === 1836) — clamp to [1836, 2026] without looping
  const nextMax = Math.max(1836, Math.min(2026, maxY + delta));

  return {
    minYear: 1836,
    maxYear: nextMax,
    selectedDecade: "all",
    showUnknownYears: nextMax >= 2026 ? state.showUnknownYears : false,
    isPlaying: false,
  };
}

/**
 * Create a reactive FilterStore instance.
 */
export function createFilterStore(initialOverrides = {}) {
  const initialLayers = {
    ...DEFAULT_FILTER_STATE.layers,
    ...(initialOverrides.layers || {}),
  };
  let initialSyncAnnex = Boolean(initialLayers.annexations);
  if (typeof initialOverrides.syncAnnexationToTime === "boolean") {
    if (
      initialOverrides.layers &&
      typeof initialOverrides.layers.annexations === "boolean"
    ) {
      initialSyncAnnex = initialOverrides.layers.annexations;
    } else {
      initialSyncAnnex = initialOverrides.syncAnnexationToTime;
      initialLayers.annexations = initialOverrides.syncAnnexationToTime;
    }
  } else {
    initialSyncAnnex = Boolean(initialLayers.annexations);
  }

  let state = {
    ...DEFAULT_FILTER_STATE,
    ...initialOverrides,
    syncAnnexationToTime: initialSyncAnnex,
    layers: initialLayers,
  };
  const listeners = new Set();

  function getState() {
    return state;
  }

  function setState(partial) {
    let nextLayers = partial.layers ? { ...state.layers, ...partial.layers } : { ...state.layers };
    let nextSyncAnnex = state.syncAnnexationToTime;

    if (partial.layers && typeof partial.layers.annexations === "boolean") {
      nextSyncAnnex = partial.layers.annexations;
    } else if (typeof partial.syncAnnexationToTime === "boolean") {
      nextSyncAnnex = partial.syncAnnexationToTime;
      nextLayers.annexations = partial.syncAnnexationToTime;
    } else {
      nextSyncAnnex = Boolean(nextLayers.annexations);
    }

    const nextLastActive =
      partial.renderMode && partial.renderMode !== "none"
        ? partial.renderMode
        : partial.lastActiveRenderMode || state.lastActiveRenderMode || "buildings";
    state = {
      ...state,
      ...partial,
      syncAnnexationToTime: nextSyncAnnex,
      lastActiveRenderMode: nextLastActive,
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
    if (state.singleLayerMode && visible) {
      const nextLayers = {};
      for (const k of Object.keys(state.layers)) {
        nextLayers[k] = k === layerKey;
      }
      setState({ layers: nextLayers, preSoloLayers: null });
      return;
    }
    setState({
      layers: {
        ...state.layers,
        [layerKey]: Boolean(visible),
      },
      preSoloLayers: null,
    });
  }

  function toggleBuildingsLayer(forceVisible = null) {
    const currentlyVisible = state.renderMode !== "none";
    const targetVisible = forceVisible !== null ? Boolean(forceVisible) : !currentlyVisible;
    if (targetVisible) {
      const nextMode =
        state.lastActiveRenderMode && state.lastActiveRenderMode !== "none"
          ? state.lastActiveRenderMode
          : "buildings";
      setState({ renderMode: nextMode });
    } else {
      const savedMode =
        state.renderMode !== "none" ? state.renderMode : state.lastActiveRenderMode || "buildings";
      setState({
        renderMode: "none",
        lastActiveRenderMode: savedMode,
      });
    }
  }

  function soloOverlayLayer(layerKey) {
    const keys = Object.keys(state.layers);
    const isCurrentlySoloed = keys.every((k) =>
      k === layerKey ? Boolean(state.layers[k]) : !state.layers[k]
    );
    if (isCurrentlySoloed) {
      const restored =
        state.preSoloLayers && Object.values(state.preSoloLayers).some(Boolean)
          ? { ...state.preSoloLayers }
          : { ...DEFAULT_FILTER_STATE.layers };
      setState({
        layers: restored,
        preSoloLayers: null,
      });
    } else {
      const activeCount = keys.filter((k) => state.layers[k]).length;
      const snapshot =
        activeCount !== 1 || !state.preSoloLayers ? { ...state.layers } : state.preSoloLayers;
      const nextLayers = {};
      for (const k of keys) {
        nextLayers[k] = k === layerKey;
      }
      setState({
        layers: nextLayers,
        preSoloLayers: snapshot,
      });
    }
  }

  function soloBuildingsOnly() {
    const keys = Object.keys(state.layers);
    const allOverlaysOff = keys.every((k) => !state.layers[k]);
    const buildingsVisible = state.renderMode !== "none";
    if (buildingsVisible && allOverlaysOff) {
      const restored =
        state.preSoloLayers && Object.values(state.preSoloLayers).some(Boolean)
          ? { ...state.preSoloLayers }
          : { ...DEFAULT_FILTER_STATE.layers };
      setState({
        layers: restored,
        preSoloLayers: null,
      });
    } else {
      const snapshot = !allOverlaysOff ? { ...state.layers } : state.preSoloLayers;
      const nextLayers = {};
      for (const k of keys) {
        nextLayers[k] = false;
      }
      const nextRenderMode =
        state.renderMode === "none"
          ? state.lastActiveRenderMode || "buildings"
          : state.renderMode;
      setState({
        renderMode: nextRenderMode,
        layers: nextLayers,
        preSoloLayers: snapshot,
      });
    }
  }

  function setAllOverlays(visible) {
    const nextLayers = {};
    for (const k of Object.keys(state.layers)) {
      nextLayers[k] = Boolean(visible);
    }
    setState({
      layers: nextLayers,
      preSoloLayers: null,
      ...(visible ? { singleLayerMode: false } : {}),
    });
  }

  function toggleSingleLayerMode(forceMode = null) {
    const nextSingle = forceMode !== null ? Boolean(forceMode) : !state.singleLayerMode;
    if (nextSingle) {
      const keys = Object.keys(state.layers);
      const activeKeys = keys.filter((k) => state.layers[k]);
      if (activeKeys.length > 1) {
        const keepKey = activeKeys[0];
        const nextLayers = {};
        for (const k of keys) {
          nextLayers[k] = k === keepKey;
        }
        setState({
          singleLayerMode: true,
          layers: nextLayers,
          preSoloLayers: { ...state.layers },
        });
        return;
      }
    }
    setState({ singleLayerMode: nextSingle });
  }

  function stepTime(direction) {
    setState(computeStepTimeState(state, direction));
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
    toggleBuildingsLayer,
    soloOverlayLayer,
    soloBuildingsOnly,
    setAllOverlays,
    toggleSingleLayerMode,
    stepTime,
    resetFilters,
    subscribe,
  };
}
