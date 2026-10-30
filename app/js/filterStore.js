/**
 * Reactive filter state store, MapLibre GL JS filter compiler, and URL hash sync
 * for The Houston Building Atlas v2.
 */

export function normalizeComparableAddress(raw) {
  return String(raw || "")
    .toUpperCase()
    .replace(/[.,#]/g, " ")
    .replace(/\bSTREET\b/g, "ST")
    .replace(/\bAVENUE\b/g, "AVE")
    .replace(/\bBOULEVARD\b/g, "BLVD")
    .replace(/\bDRIVE\b/g, "DR")
    .replace(/\bROAD\b/g, "RD")
    .replace(/\bLANE\b/g, "LN")
    .replace(/\bCOURT\b/g, "CT")
    .replace(/\bPLACE\b/g, "PL")
    .replace(/\bPARKWAY\b|\bPKY\b/g, "PKWY")
    .replace(/\bFREEWAY\b/g, "FWY")
    .replace(/\bHIGHWAY\b/g, "HWY")
    .replace(/\bNORTH\b/g, "N")
    .replace(/\bSOUTH\b/g, "S")
    .replace(/\bEAST\b/g, "E")
    .replace(/\bWEST\b/g, "W")
    .replace(/\s+/g, " ")
    .trim();
}

export const DEFAULT_FILTER_STATE = {
  colorMode: "year_built", // 'year_built' | 'preservation_status' | 'use_category'
  paletteStyle: "archival", // 'archival' | 'classic_ee'
  renderMode: "buildings", // 'buildings' | 'both' | 'parcels' | 'none'
  lastActiveRenderMode: "buildings", // remembers 'buildings' | 'both' | 'parcels' when buildings are toggled off
  basemap: "dark_archival", // 'dark_archival' | 'warm_parchment' | 'satellite' | 'solid_dark' | 'solid_light'
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
  historicMapId: "sanborn_1907", // active historical / Sanborn raster map ID from HISTORICAL_MAP_CATALOG
  historicMapOpacity: 82, // 15..100 opacity percentage for the Historical / Sanborn Map overlay
  historicMapSpyglass: false, // when true, reveals the historical map inside a movable circular spyglass lens
  historicMapSpyglassRadius: 152, // 72..360px radius for the circular spyglass lens (adjusted via [ / ] keys or slider)
  historicalSnapshotMode: false, // when true, hides structures not yet built or already demolished at maxYear
  wardEra: 1903, // 1839 | 1866 | 1896 | 1903 (1903-1905 Final Aldermanic Charter)
  showAnnexationSpokes: true, // include 1963 10-ft highway ETJ spokes & 2000s-2010s MUD SPA commercial strips
  landmarkFilter: "all", // 'all' | 'protected' (286 PLM + LM-in-HD) | 'waiver_lm' (223 90-day waiver LMs)
  redBookCategory: "all", // 'all' | 'church' | 'school' | 'business' | 'residence'
  holcGradeFilter: "all", // 'all' | 'A' | 'B' | 'C' | 'D'
  hahcFilter: "all", // 'all' | 'active_agenda' | 'demolition_relocation' | 'addition_new' | 'designation' | 'denied_or_cor'
  isolatedBoundary: null, // null or { layerKey, id, name, mode: 'contents' | 'footprints_only' | 'border_only' }
  activeScorecard: null, // null or { layer: string, id: string }
  activeCh42: null, // null or { hcad: string, includeOpposing: boolean }
  layers: {
    permitWatch: true,
    platWatch: false,
    hahcTracker: false,
    potentialHistoricDistricts: false,
    goodBrickAwards: true,
    landmarks: true,
    historicDistricts: true,
    heritageDistricts: true,
    nrhpDistricts: false,
    nrhpProperties: false,
    thcMarkers: false,
    culturalDistricts: false,
    redBook1915: false,
    holc1937: false,
    neighborhoods: false,
    plattedSubdivisions: false,
    landUseProtections: false,
    superNeighborhoods: false,
    councilDistricts: false,
    historicWards: false,
    annexations: false,
    historicalWaterways: false,
    historicalRailroads: false,
    lostHouston: true,
    relocatedStructures: false,
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
      if (state.historicalSnapshotMode) {
        return [
          "all",
          [">=", ["to-number", ["get", "year_built"], 0], 1836],
          ["<=", ["to-number", ["get", "year_built"], 0], decInt + 9],
        ];
      }
      return [
        "all",
        [">=", ["to-number", ["get", "year_built"], 0], lowBound],
        ["<=", ["to-number", ["get", "year_built"], 0], decInt + 9],
      ];
    }
  }

  const effectiveMinY = state.historicalSnapshotMode ? 1836 : minY;
  const datedInRange = [
    "all",
    [">=", ["to-number", ["get", "year_built"], 0], effectiveMinY],
    ["<=", ["to-number", ["get", "year_built"], 0], maxY],
  ];

  if (state.showUnknownYears && !state.historicalSnapshotMode) {
    return [
      "any",
      ["<", ["to-number", ["get", "year_built"], 0], 1836],
      datedInRange,
    ];
  }

  return datedInRange;
}

/**
 * Resolve normalized temporal bounds and snapshot mode for Lost Houston filtering.
 * Shared by both WebGL (`buildLostHoustonFilterExpression`) and 2D Canvas / JS
 * (`featureMatchesLostHoustonFilter`) so boundary math never drifts.
 */
export function resolveLostHoustonBounds(state) {
  const minY = Number(state.minYear) || 1836;
  const maxY = Number(state.maxYear) || 2026;
  const dec = String(state.selectedDecade || "all");
  let lowBound = minY;
  let highBound = maxY;
  if (dec !== "all" && dec !== "unknown") {
    const decInt = Number(dec);
    if (!Number.isNaN(decInt) && decInt >= 1830) {
      lowBound = decInt === 1840 ? 1836 : decInt;
      highBound = decInt + 9;
    }
  }
  const isSnapshot = Boolean(
    state.historicalSnapshotMode || (state.isPlaying && highBound < 2026)
  );
  return { dec, lowBound, highBound, isSnapshot };
}

const LOST_YEAR_BUILT_EXPR = [
  "case",
  [">", ["to-number", ["get", "year_built"], 0], 0],
  ["to-number", ["get", "year_built"], 1836],
  1836,
];

const LOST_YEAR_DEMO_EXPR = [
  "case",
  [">", ["to-number", ["get", "year_demolished"], 0], 0],
  ["to-number", ["get", "year_demolished"], 2026],
  2026,
];

/**
 * Compile a MapLibre GL JS filter expression for the Lost Houston (demolished structures) layer.
 * In Historical Snapshot Mode (or during time-lapse playback before 2026), displays only structures
 * that were standing in the active target year (`year_built <= cutoff && year_demolished > cutoff`).
 * Otherwise displays all demolished structures whose lifespan overlaps `[minYear, maxYear]`.
 */
export function buildLostHoustonFilterExpression(state) {
  if (!state) return ["has", "year_built"];
  const { dec, lowBound, highBound, isSnapshot } = resolveLostHoustonBounds(state);

  if (dec === "unknown") {
    return ["==", ["get", "id"], "__none__"];
  }

  if (isSnapshot) {
    return [
      "all",
      ["<=", LOST_YEAR_BUILT_EXPR, highBound],
      [">", LOST_YEAR_DEMO_EXPR, highBound],
    ];
  }

  if (lowBound <= 1836 && highBound >= 2026) {
    return ["has", "year_built"];
  }

  return [
    "all",
    ["<=", LOST_YEAR_BUILT_EXPR, highBound],
    [">=", LOST_YEAR_DEMO_EXPR, lowBound],
  ];
}

/**
 * Evaluate whether a Lost Houston feature passes the exact same temporal / snapshot filter
 * compiled by `buildLostHoustonFilterExpression(state)` (guarantees 1:1 WebGL <-> 2D Canvas parity).
 */
export function featureMatchesLostHoustonFilter(props, state) {
  if (!props) return false;
  const hasYearBuilt = props.year_built !== undefined && props.year_built !== null;
  if (!state) return hasYearBuilt;
  const { dec, lowBound, highBound, isSnapshot } = resolveLostHoustonBounds(state);
  if (dec === "unknown") return false;

  const rawYb = Number(props.year_built);
  const rawYd = Number(props.year_demolished);
  const yb = Number.isFinite(rawYb) && rawYb > 0 ? rawYb : 1836;
  const yd = Number.isFinite(rawYd) && rawYd > 0 ? rawYd : 2026;
  if (isSnapshot) {
    return yb <= highBound && yd > highBound;
  }
  if (lowBound <= 1836 && highBound >= 2026) {
    return hasYearBuilt;
  }
  return yb <= highBound && yd >= lowBound;
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

export const HISTORIC_WARD_ERAS = [1839, 1866, 1896, 1903];

/**
 * Resolve the active Historic Ward boundary era (1839, 1866, 1896, or 1903).
 * Houston's aldermanic ward system was abolished in the 1905 City Charter, making the
 * 1903 charter boundary (1903-1905) the final official municipal ward map.
 * When time-lapse playback (`isPlaying`) is active, steps through the
 * historical ward charters in lockstep with the timeline; otherwise uses `state.wardEra`.
 */
export function resolveActiveWardEra(state) {
  if (!state) return 1903;
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
  const explicitEra = Number(state.wardEra) || 1903;
  return HISTORIC_WARD_ERAS.includes(explicitEra) ? explicitEra : 1903;
}

/**
 * Compile a MapLibre filter expression for the Historic Wards layer.
 */
export function buildHistoricWardFilterExpression(state) {
  const era = resolveActiveWardEra(state);
  return ["==", ["to-number", ["get", "era"], 1903], era];
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

/**
 * Compile a MapLibre filter expression for the COH Landmarks layer based on `landmarkFilter`
 * ('all' | 'protected' | 'waiver_lm') and optional isolation ID list.
 */
export function buildLandmarksFilterExpression(state, isoIds = null) {
  const mode = String(state?.landmarkFilter || "all");
  const clauses = [["!", ["boolean", ["get", "demolished"], false]]];
  if (mode === "protected") {
    clauses.push([
      "in",
      ["coalesce", ["get", "protection_tier"], ""],
      ["literal", ["plm", "lm_in_hd"]],
    ]);
  } else if (mode === "waiver_lm") {
    clauses.push(["==", ["coalesce", ["get", "protection_tier"], ""], "lm_waiver_vulnerable"]);
  }
  if (Array.isArray(isoIds)) {
    clauses.push(["in", ["coalesce", ["get", "id"], ["get", "hcad_num"], ""], ["literal", isoIds]]);
  }
  if (!clauses.length) return null;
  if (clauses.length === 1) return clauses[0];
  return ["all", ...clauses];
}

/**
 * Compile a MapLibre filter expression for the 1915 Red Book of Houston layer
 * based on `redBookCategory` ('all' | 'church' | 'school' | 'business' | 'residence')
 * and optional isolation ID list.
 */
export function buildRedBookFilterExpression(state, isoIds = null) {
  const cat = String(state?.redBookCategory || "all").trim().toLowerCase();
  const clauses = [];
  if (cat === "church" || cat === "school" || cat === "business" || cat === "residence") {
    clauses.push(["==", ["coalesce", ["get", "category"], ""], cat]);
  }
  if (Array.isArray(isoIds)) {
    clauses.push(["in", ["coalesce", ["get", "id"], ""], ["literal", isoIds]]);
  }
  if (!clauses.length) return null;
  if (clauses.length === 1) return clauses[0];
  return ["all", ...clauses];
}

/**
 * Compile a MapLibre filter expression for the 1937 HOLC Neighborhood Security Zones layer
 * based on `holcGradeFilter` ('all' | 'A' | 'B' | 'C' | 'D').
 */
export function buildHolcZonesFilterExpression(state) {
  const grade = String(state?.holcGradeFilter || "all").trim().toUpperCase();
  if (grade === "A" || grade === "B" || grade === "C" || grade === "D") {
    return ["==", ["coalesce", ["get", "holc_grade"], ""], grade];
  }
  return null;
}

/**
 * Compile a MapLibre filter expression for the HAHC COA & Designation Tracker layer.
 */
export function buildHahcFilterExpression(state, isoIds = null) {
  const f = String(state?.hahcFilter || "all").trim().toLowerCase();
  const clauses = [];
  if (f === "active_agenda") {
    clauses.push(["==", ["boolean", ["get", "has_active_agenda"], false], true]);
  } else if (f === "demolition_relocation") {
    clauses.push(["==", ["boolean", ["get", "has_demolition_relocation"], false], true]);
  } else if (f === "designation") {
    clauses.push(["==", ["boolean", ["get", "has_designation"], false], true]);
  } else if (f === "denied_or_cor") {
    clauses.push(["==", ["boolean", ["get", "has_denied_or_cor"], false], true]);
  } else if (f === "addition_new") {
    clauses.push(["!", ["boolean", ["get", "has_demolition_relocation"], false]]);
    clauses.push(["!", ["boolean", ["get", "has_designation"], false]]);
  }
  if (Array.isArray(isoIds)) {
    clauses.push(["in", ["coalesce", ["get", "id"], ""], ["literal", isoIds]]);
  }
  if (!clauses.length) return null;
  if (clauses.length === 1) return clauses[0];
  return ["all", ...clauses];
}

function layersMatchDefault(layers) {
  if (!layers) return true;
  const def = DEFAULT_FILTER_STATE.layers;
  return Object.keys(def).every((k) => Boolean(layers[k]) === Boolean(def[k]));
}

const LAYER_KEY_TO_SHORT = {
  permitWatch: "permit_watch",
  platWatch: "plat_watch",
  hahcTracker: "hahc_coa",
  potentialHistoricDistricts: "potential_districts",
  goodBrickAwards: "good_brick",
  landmarks: "landmarks",
  historicDistricts: "historic_districts",
  heritageDistricts: "heritage_districts",
  nrhpDistricts: "nrhp_districts",
  nrhpProperties: "nrhp_properties",
  thcMarkers: "thc_markers",
  culturalDistricts: "cultural_districts",
  redBook1915: "red_book",
  holc1937: "holc_zones",
  neighborhoods: "neighborhoods",
  plattedSubdivisions: "platted_subdivisions",
  landUseProtections: "land_use_protections",
  councilDistricts: "council_districts",
  superNeighborhoods: "super_neighborhoods",
  historicWards: "historic_wards",
  annexations: "annexations",
  historicalWaterways: "waterways",
  historicalRailroads: "railroads",
  lostHouston: "lost_houston",
  relocatedStructures: "relocated_structures",
  historicMap: "historic_map",
};

const SHORT_TO_LAYER_KEY = {
  permitwatch: "permitWatch",
  permit_watch: "permitWatch",
  permit_alerts: "permitWatch",
  demolition_watch: "permitWatch",
  endangered: "permitWatch",
  platwatch: "platWatch",
  plat_watch: "platWatch",
  planning_commission: "platWatch",
  hahctracker: "hahcTracker",
  hahc_tracker: "hahcTracker",
  hahc_coa: "hahcTracker",
  hahc: "hahcTracker",
  coa: "hahcTracker",
  hahc_coa_filings: "hahcTracker",
  potentialhistoricdistricts: "potentialHistoricDistricts",
  potential_historic_districts: "potentialHistoricDistricts",
  potential_districts: "potentialHistoricDistricts",
  potential_hd: "potentialHistoricDistricts",
  phd: "potentialHistoricDistricts",
  surveyed_districts: "potentialHistoricDistricts",
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
  nrhpproperties: "nrhpProperties",
  nrhp_properties: "nrhpProperties",
  nrhp_listings: "nrhpProperties",
  nrhp_sites: "nrhpProperties",
  nrhp_pts: "nrhpProperties",
  thcmarkers: "thcMarkers",
  thc_markers: "thcMarkers",
  thc: "thcMarkers",
  markers: "thcMarkers",
  culturaldistricts: "culturalDistricts",
  cultural_districts: "culturalDistricts",
  cultural: "culturalDistricts",
  arts_districts: "culturalDistricts",
  red_book: "redBook1915",
  redbook: "redBook1915",
  red_book_1915: "redBook1915",
  redbook1915: "redBook1915",
  holc: "holc1937",
  holc_1937: "holc1937",
  holc1937: "holc1937",
  holc_zones: "holc1937",
  holc_1937_zones: "holc1937",
  redlining: "holc1937",
  neighborhoods: "neighborhoods",
  neighborhood: "neighborhoods",
  nbhd: "neighborhoods",
  plattedsubdivisions: "plattedSubdivisions",
  platted_subdivisions: "plattedSubdivisions",
  subdivisions: "plattedSubdivisions",
  subdivision: "plattedSubdivisions",
  plats: "plattedSubdivisions",
  plat: "plattedSubdivisions",
  landuseprotections: "landUseProtections",
  land_use_protections: "landUseProtections",
  protections: "landUseProtections",
  deeds: "landUseProtections",
  deed_restrictions: "landUseProtections",
  smls: "landUseProtections",
  smbl: "landUseProtections",
  councildistricts: "councilDistricts",
  council_districts: "councilDistricts",
  council_district: "councilDistricts",
  council: "councilDistricts",
  city_council: "councilDistricts",
  cd: "councilDistricts",
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
  historicalwaterways: "historicalWaterways",
  historical_waterways: "historicalWaterways",
  waterways: "historicalWaterways",
  waterway: "historicalWaterways",
  bayous: "historicalWaterways",
  creeks: "historicalWaterways",
  streams: "historicalWaterways",
  gullies: "historicalWaterways",
  historicalrailroads: "historicalRailroads",
  historical_railroads: "historicalRailroads",
  railroads: "historicalRailroads",
  railroad: "historicalRailroads",
  rail: "historicalRailroads",
  trains: "historicalRailroads",
  streetcars: "historicalRailroads",
  losthouston: "lostHouston",
  lost_houston: "lostHouston",
  lost: "lostHouston",
  demolished: "lostHouston",
  relocatedstructures: "relocatedStructures",
  relocated_structures: "relocatedStructures",
  relocated: "relocatedStructures",
  moved: "relocatedStructures",
  permits: "relocatedStructures",
  historicmap: "historicMap",
  historic_map: "historicMap",
  sanborn: "historicMap",
  topo: "historicMap",
  usgs: "historicMap",
};

const SCORECARD_LAYER_ALIASES = {
  council_districts: "council_districts",
  councildistricts: "council_districts",
  council_district: "council_districts",
  council: "council_districts",
  city_council: "council_districts",
  cd: "council_districts",
  historic_districts: "historic_districts",
  historicdistricts: "historic_districts",
  districts: "historic_districts",
  hd: "historic_districts",
  heritage_districts: "heritage_districts",
  heritagedistricts: "heritage_districts",
  heritage: "heritage_districts",
  nrhp_districts: "nrhp_districts",
  nrhpdistricts: "nrhp_districts",
  nrhp: "nrhp_districts",
  potential_historic_districts: "potential_historic_districts",
  potentialhistoricdistricts: "potential_historic_districts",
  potential_districts: "potential_historic_districts",
  potential_hd: "potential_historic_districts",
  phd: "potential_historic_districts",
  super_neighborhoods: "super_neighborhoods",
  superneighborhoods: "super_neighborhoods",
  super_neighborhood: "super_neighborhoods",
  snbr: "super_neighborhoods",
  holc_1937_zones: "holc_1937_zones",
  holc_zones: "holc_1937_zones",
  holc_1937: "holc_1937_zones",
  holc1937: "holc_1937_zones",
  holc: "holc_1937_zones",
  neighborhoods: "neighborhoods",
  neighborhood: "neighborhoods",
  nbhd: "neighborhoods",
  platted_subdivisions: "platted_subdivisions",
  plattedsubdivisions: "platted_subdivisions",
  subdivisions: "platted_subdivisions",
  subdivision: "platted_subdivisions",
  plats: "platted_subdivisions",
  plat: "platted_subdivisions",
};

const OVERLAY_DATA_ALIAS_MAP = Object.freeze({
  historicwards: "historic_wards",
  historic_wards: "historic_wards",
  historic_ward: "historic_wards",
  superneighborhoods: "super_neighborhoods",
  super_neighborhoods: "super_neighborhoods",
  super_neighborhood: "super_neighborhoods",
  snbr: "super_neighborhoods",
  councildistricts: "council_districts",
  council_districts: "council_districts",
  council_district: "council_districts",
  city_council: "council_districts",
  council: "council_districts",
  cd: "council_districts",
  plattedsubdivisions: "platted_subdivisions",
  platted_subdivisions: "platted_subdivisions",
  platted_subdivision: "platted_subdivisions",
  subdivisions: "platted_subdivisions",
  subdivision: "platted_subdivisions",
  plats: "platted_subdivisions",
  plat: "platted_subdivisions",
  potentialhistoricdistricts: "potential_historic_districts",
  potential_historic_districts: "potential_historic_districts",
  potential_historic_district: "potential_historic_districts",
  potential_districts: "potential_historic_districts",
  potential_hd: "potential_historic_districts",
  phd: "potential_historic_districts",
  culturaldistricts: "cultural_districts",
  cultural_districts: "cultural_districts",
  cultural_district: "cultural_districts",
  holc1937: "holc_1937_zones",
  holc_1937_zones: "holc_1937_zones",
  holc_1937_zone: "holc_1937_zones",
  holc_1937: "holc_1937_zones",
  holc_zones: "holc_1937_zones",
  holc: "holc_1937_zones",
  redbook1915: "red_book_1915",
  red_book_1915: "red_book_1915",
  red_book: "red_book_1915",
  losthouston: "lost_houston",
  lost_houston: "lost_houston",
  historicalwaterways: "historical_waterways",
  historical_waterways: "historical_waterways",
  historical_waterway: "historical_waterways",
  waterways: "historical_waterways",
  historicalrailroads: "historical_railroads",
  historical_railroads: "historical_railroads",
  historical_railroad: "historical_railroads",
  railroads: "historical_railroads",
  historicdistricts: "historic_districts",
  historic_districts: "historic_districts",
  historic_district: "historic_districts",
  districts: "historic_districts",
  hd: "historic_districts",
  heritagedistricts: "heritage_districts",
  heritage_districts: "heritage_districts",
  heritage_district: "heritage_districts",
  heritage: "heritage_districts",
  nrhpdistricts: "nrhp_districts",
  nrhp_districts: "nrhp_districts",
  nrhp_district: "nrhp_districts",
  nrhp: "nrhp_districts",
  nrhpproperties: "nrhp_properties",
  nrhp_properties: "nrhp_properties",
  nrhp_property: "nrhp_properties",
  hahctracker: "hahc_coa_filings",
  hahc_coa_filings: "hahc_coa_filings",
  hahc_coa: "hahc_coa_filings",
  hahc_tracker: "hahc_coa_filings",
  landuseprotections: "land_use_protections",
  land_use_protections: "land_use_protections",
  neighborhoods: "neighborhoods",
  neighborhood: "neighborhoods",
  nbhd: "neighborhoods",
  landmarks: "landmarks",
  thcmarkers: "thc_markers",
  thc_markers: "thc_markers",
  goodbrickawards: "good_brick_awards",
  good_brick_awards: "good_brick_awards",
  annexations: "annexations",
  relocatedstructures: "relocated_structures",
  relocated_structures: "relocated_structures",
});

export function toOverlayDataKey(rawLayer = "", rawId = "", fallback = "") {
  const idLower = String(rawId || "").trim().toLowerCase();
  if (idLower.startsWith("holc_1937_")) return "holc_1937_zones";
  if (idLower.startsWith("plat_")) return "platted_subdivisions";
  if (idLower.startsWith("council_dist_") || /^cd_[a-k]$/.test(idLower)) return "council_districts";
  const cleanLayer = String(rawLayer || "").trim();
  const mapped = OVERLAY_DATA_ALIAS_MAP[cleanLayer.toLowerCase()];
  if (mapped) return mapped;
  return cleanLayer || fallback;
}

export function toLayerStateKey(overlayOrAlias = "") {
  const clean = String(overlayOrAlias || "").trim();
  if (!clean) return "";
  if (Object.prototype.hasOwnProperty.call(DEFAULT_FILTER_STATE.layers, clean)) {
    return clean;
  }
  return SHORT_TO_LAYER_KEY[clean.toLowerCase()] || "";
}

function normalizeScorecardSpec(spec) {
  if (!spec || typeof spec !== "object") return null;
  const rawLayer = String(spec.layer || spec.layerKey || "").trim().toLowerCase();
  const layer = SCORECARD_LAYER_ALIASES[rawLayer] || "";
  const id = String(spec.id || "").trim();
  if (!layer || !id || !/^[A-Za-z0-9_.:-]+$/.test(id)) return null;
  return { layer, id };
}

function normalizeCh42Spec(spec) {
  if (!spec) return null;
  const rawHcad = typeof spec === "string" ? spec : spec.hcad || spec.hcadNum || "";
  const hcad = String(rawHcad || "").trim();
  if (!/^\d{13}$/.test(hcad) || /^0+$/.test(hcad)) return null;
  return {
    hcad,
    includeOpposing: Boolean(
      typeof spec === "object" && (spec.includeOpposing || spec.opposing || spec.opp)
    ),
  };
}

const BASEMAP_TO_SHORT = {
  dark_archival: "dark",
  warm_parchment: "light",
  satellite: "satellite",
  solid_dark: "solid_dark",
  solid_light: "solid_light",
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
  solid_dark: "solid_dark",
  nodark: "solid_dark",
  blank_dark: "solid_dark",
  none_dark: "solid_dark",
  none: "solid_dark",
  blank: "solid_dark",
  solid_light: "solid_light",
  nolight: "solid_light",
  blank_light: "solid_light",
  none_light: "solid_light",
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
    description: "1836-1939 structures across Houston with all overlay pins hidden for pure architectural footprint clarity.",
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
    selectedBoundary = null,
    activeChapterId = "",
    activePortfolio = null,
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
  if (state.layers?.historicMap && state.historicMapId && state.historicMapId !== "sanborn_1907") {
    params.set("histMap", String(state.historicMapId));
  }
  if (state.layers?.historicMap && Number(state.historicMapOpacity) !== 82) {
    params.set("histOpacity", String(Math.round(Number(state.historicMapOpacity) || 82)));
  }
  if (state.layers?.historicMap && state.historicMapSpyglass) {
    params.set("spyglass", "1");
  }
  if (state.historicalSnapshotMode) {
    params.set("snapshot", "1");
  }
  if (state.layers?.historicWards && Number(state.wardEra) && Number(state.wardEra) !== 1903) {
    params.set("wardEra", String(Number(state.wardEra)));
  }
  if (
    state.layers?.landmarks &&
    state.landmarkFilter &&
    state.landmarkFilter !== "all"
  ) {
    params.set("lmFilter", String(state.landmarkFilter));
  }
  if (
    state.layers?.redBook1915 &&
    state.redBookCategory &&
    state.redBookCategory !== "all"
  ) {
    params.set("rbCat", String(state.redBookCategory));
  }
  if (
    state.layers?.holc1937 &&
    state.holcGradeFilter &&
    state.holcGradeFilter !== "all"
  ) {
    params.set("holcGrade", String(state.holcGradeFilter));
  }
  if (
    state.layers?.hahcTracker &&
    state.hahcFilter &&
    state.hahcFilter !== "all"
  ) {
    params.set("hahcFilter", String(state.hahcFilter));
  }

  if (activeChapterId) {
    params.set("chapter", String(activeChapterId).trim());
  }
  if (activePortfolio && activePortfolio.query) {
    const pKind = activePortfolio.kind === "style" ? "style" : "architect";
    params.set("portfolio", `${pKind}:${String(activePortfolio.query).trim()}`);
  }

  const effScorecard = normalizeScorecardSpec(
    options.activeScorecard !== undefined ? options.activeScorecard : state.activeScorecard
  );
  if (effScorecard) {
    params.set("scorecard", `${effScorecard.layer}:${effScorecard.id}`);
  }

  const effCh42 = normalizeCh42Spec(
    options.activeCh42 !== undefined ? options.activeCh42 : state.activeCh42
  );
  if (effCh42) {
    params.set("ch42", effCh42.hcad);
    if (effCh42.includeOpposing) {
      params.set("ch42opp", "1");
    }
  }

  if (state.isolatedBoundary && (state.isolatedBoundary.id || state.isolatedBoundary.name)) {
    const isoLayer = state.isolatedBoundary.layerKey || "neighborhoods";
    const isoTarget = state.isolatedBoundary.id || state.isolatedBoundary.name;
    params.set("isolate", `${isoLayer}:${isoTarget}`);
    if (state.isolatedBoundary.mode && state.isolatedBoundary.mode !== "contents") {
      params.set("isoMode", state.isolatedBoundary.mode);
    }
  }

  if (selectedHcad) {
    params.set("hcad", String(selectedHcad).trim());
  } else if (effectiveId) {
    params.set("id", String(effectiveId).trim());
  } else if (selectedBoundary && (selectedBoundary.id || selectedBoundary.name)) {
    const bndLayer = selectedBoundary.layerKey || "neighborhoods";
    const bndTarget = selectedBoundary.id || selectedBoundary.name;
    params.set("bnd", `${bndLayer}:${bndTarget}`);
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
    selectedBoundary: options.selectedBoundary || null,
    activeChapterId: options.activeChapterId || "",
    activePortfolio: options.activePortfolio || null,
    activeScorecard: options.activeScorecard,
    activeCh42: options.activeCh42,
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
  if (
    cleanSearch &&
    cleanHash &&
    !cleanHash.includes("=") &&
    /^[A-Za-z0-9_.-]+$/.test(cleanHash) &&
    /^\d{10,13}$/.test(String(mergedParams.get("id") || "").trim())
  ) {
    mergedParams.set("id", `${String(mergedParams.get("id")).trim()}#${cleanHash}`);
    mergedParams.delete(cleanHash);
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
  const rawHistMap = (mergedParams.get("histMap") || mergedParams.get("historicMapId") || "").trim();
  if (rawHistMap) {
    patch.historicMapId = rawHistMap;
  }
  const rawHistOp = mergedParams.get("histOpacity") || mergedParams.get("topoOpacity");
  if (rawHistOp !== null && rawHistOp !== undefined && rawHistOp !== "") {
    patch.historicMapOpacity = Math.max(15, Math.min(100, parseInt(rawHistOp, 10) || 82));
  }
  if (mergedParams.get("spyglass") === "1" || mergedParams.get("spyglass") === "true") {
    patch.historicMapSpyglass = true;
  }
  if (mergedParams.get("snapshot") === "1" || mergedParams.get("snapshot") === "true") {
    patch.historicalSnapshotMode = true;
  }
  const rawWardEra = mergedParams.get("wardEra") || mergedParams.get("ward_era");
  if (rawWardEra !== null && rawWardEra !== undefined && rawWardEra !== "") {
    const parsedEra = parseInt(rawWardEra, 10);
    if (HISTORIC_WARD_ERAS.includes(parsedEra)) {
      patch.wardEra = parsedEra;
    }
  }
  const rawLmFilter = (
    mergedParams.get("lmFilter") ||
    mergedParams.get("landmarkFilter") ||
    ""
  )
    .trim()
    .toLowerCase();
  if (rawLmFilter === "protected" || rawLmFilter === "waiver_lm" || rawLmFilter === "all") {
    patch.landmarkFilter = rawLmFilter;
  } else if (rawLmFilter === "vulnerable" || rawLmFilter === "waiver") {
    patch.landmarkFilter = "waiver_lm";
  }
  const rawRbCat = (
    mergedParams.get("rbCat") ||
    mergedParams.get("redBookCategory") ||
    ""
  )
    .trim()
    .toLowerCase();
  if (
    rawRbCat === "church" ||
    rawRbCat === "school" ||
    rawRbCat === "business" ||
    rawRbCat === "residence" ||
    rawRbCat === "all"
  ) {
    patch.redBookCategory = rawRbCat;
  }
  const rawHolcGrade = (
    mergedParams.get("holcGrade") ||
    mergedParams.get("holcGradeFilter") ||
    ""
  )
    .trim()
    .toUpperCase();
  if (
    rawHolcGrade === "A" ||
    rawHolcGrade === "B" ||
    rawHolcGrade === "C" ||
    rawHolcGrade === "D"
  ) {
    patch.holcGradeFilter = rawHolcGrade;
  } else if (rawHolcGrade === "ALL") {
    patch.holcGradeFilter = "all";
  }
  const rawHahcFilter = (
    mergedParams.get("hahcFilter") ||
    mergedParams.get("hahc_filter") ||
    ""
  )
    .trim()
    .toLowerCase();
  if (
    rawHahcFilter === "active_agenda" ||
    rawHahcFilter === "demolition_relocation" ||
    rawHahcFilter === "addition_new" ||
    rawHahcFilter === "designation" ||
    rawHahcFilter === "denied_or_cor" ||
    rawHahcFilter === "all"
  ) {
    patch.hahcFilter = rawHahcFilter;
  }

  // Isolated Boundary (`isolate` & `isoMode`)
  const rawIsolate = (mergedParams.get("isolate") || "").trim();
  if (rawIsolate) {
    const colonIdx = rawIsolate.indexOf(":");
    let isoLayer = "";
    let isoTarget = rawIsolate;
    if (colonIdx > 0) {
      isoLayer = rawIsolate.slice(0, colonIdx).trim();
      isoTarget = rawIsolate.slice(colonIdx + 1).trim();
    }
    const rawIsoMode = (mergedParams.get("isoMode") || "contents").trim().toLowerCase();
    const validIsoMode =
      rawIsoMode === "footprints_only" || rawIsoMode === "border_only"
        ? rawIsoMode
        : "contents";
    if (isoTarget) {
      patch.isolatedBoundary = {
        layerKey: isoLayer || "neighborhoods",
        id: isoTarget,
        name: isoTarget,
        mode: validIsoMode,
      };
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

  // Optional Selected Property (`hcad` or `id` or `selected` or `q` or `bnd`)
  const selHcad = (mergedParams.get("hcad") || "").trim();
  const selId = (mergedParams.get("id") || mergedParams.get("selected") || "").trim();
  const selQuery = (
    mergedParams.get("q") ||
    mergedParams.get("query") ||
    mergedParams.get("address") ||
    ""
  ).trim();
  const selBnd = (mergedParams.get("bnd") || "").trim();
  const effectiveHcad =
    selHcad || (/^\d{10,13}$/.test(selQuery) ? selQuery.padStart(13, "0") : "");
  if (effectiveHcad || selId || selQuery) {
    selection = {
      hcadNum: effectiveHcad,
      featureId: selId,
      query: selQuery,
    };
  } else if (selBnd) {
    const colonIdx = selBnd.indexOf(":");
    const bndLayer =
      colonIdx > 0
        ? SHORT_TO_LAYER_KEY[selBnd.slice(0, colonIdx)] || selBnd.slice(0, colonIdx)
        : "neighborhoods";
    const bndTarget = colonIdx > 0 ? selBnd.slice(colonIdx + 1).trim() : selBnd;
    if (bndTarget) {
      selection = { boundaryLayer: bndLayer, boundaryId: bndTarget };
    }
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

  const activeChapterId = (mergedParams.get("chapter") || "").trim();
  const rawPortfolio = (mergedParams.get("portfolio") || "").trim();
  let activePortfolio = null;
  if (rawPortfolio) {
    const cIdx = rawPortfolio.indexOf(":");
    if (cIdx > 0) {
      const pKind = rawPortfolio.slice(0, cIdx).trim().toLowerCase() === "style" ? "style" : "architect";
      const pQuery = rawPortfolio.slice(cIdx + 1).trim();
      if (pQuery) activePortfolio = { kind: pKind, query: pQuery };
    } else {
      activePortfolio = { kind: "architect", query: rawPortfolio };
    }
  }

  let activeScorecard = null;
  if (mergedParams.has("scorecard") || mergedParams.has("scorecards")) {
    const rawSc = String(
      mergedParams.get("scorecard") ?? mergedParams.get("scorecards") ?? ""
    ).trim();
    const scIdx = rawSc.indexOf(":");
    if (scIdx > 0) {
      activeScorecard = normalizeScorecardSpec({
        layer: rawSc.slice(0, scIdx),
        id: rawSc.slice(scIdx + 1),
      });
    } else if (rawSc === "hub" || rawSc === "1" || rawSc === "true") {
      activeScorecard = { layer: "council_districts", id: "__hub__" };
    }
    patch.activeScorecard = activeScorecard;
  }

  let activeCh42 = null;
  if (mergedParams.has("ch42")) {
    const rawCh42 = (mergedParams.get("ch42") || "").trim();
    const rawOpp = (mergedParams.get("ch42opp") || "").trim().toLowerCase();
    const includeOpposing = rawOpp === "1" || rawOpp === "true" || rawOpp === "on";
    activeCh42 = normalizeCh42Spec({ hcad: rawCh42, includeOpposing });
    patch.activeCh42 = activeCh42;
  }

  return {
    patch,
    viewport,
    selection,
    collapseSidebar,
    activeChapterId,
    activePortfolio,
    activeScorecard,
    activeCh42,
  };
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

  // Case 2: Cumulative growth mode (minYear === 1836) - clamp to [1836, 2026] without looping
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
      redBookCategory: "all",
      holcGradeFilter: "all",
      hahcFilter: "all",
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
