/**
 * MapLibre-agnostic HCAD countywide viewport parcel client, LRU bbox cache,
 * quadtree transfer-limit splitter, Pilot Core deduplicator, and multi-building
 * minimum-age enricher for The Houston Building Atlas.
 */

import { isEntitySuppressedByOverrides } from "./curatedEdits.js?v=20261030e";
import { computeGeoJsonCentroid } from "./platMonitorHelpers.js?v=20261030e";

export const HCAD_PUBLIC_QUERY_URL =
  "https://arcweb.hcad.org/server/rest/services/public/public_query/MapServer/0/query";

export const HCAD_PUBLIC_EXPORT_TILE_URL =
  "https://arcweb.hcad.org/server/rest/services/public/public_query/MapServer/export?bbox={bbox-epsg-3857}&bboxSR=3857&imageSR=3857&size=512,512&format=png32&transparent=true&f=image";

/**
 * Normalizes a raw HCAD account identifier to a canonical 13-digit numeric string.
 * Left-pads 10-to-12 digit integer strings stripped by spreadsheet exports while
 * rejecting scientific-notation or non-numeric strings.
 *
 * @param {unknown} raw
 * @returns {string}
 */
export function normalizeHcad13(raw) {
  if (raw === null || raw === undefined) return "";
  const base = String(raw).split("#")[0].trim();
  if (!base || /[eE+]/.test(base)) return "";
  const digits = base.replace(/[\s-]/g, "");
  if (!/^\d{10,13}$/.test(digits) || /^0+$/.test(digits)) return "";
  return digits.padStart(13, "0");
}

/**
 * Maps an HCAD state property class code (e.g. A1, B2, F1, C1, X1) to an Atlas use category.
 *
 * @param {string} stateClass
 * @param {number} yearBuilt
 * @returns {string}
 */
export function mapHcadStateClassToUseCategory(stateClass, yearBuilt = 0) {
  const code = String(stateClass || "").trim().toUpperCase();
  if (/^[AB]/.test(code) || code.startsWith("O")) return "Residential";
  if (/^F/.test(code)) return "Commercial";
  if (/^[DGJLM]/.test(code)) return "Industrial";
  if (/^X/.test(code)) return "Civic / Institutional";
  if (/^C/.test(code)) return yearBuilt >= 1836 ? "Residential" : "Vacant / Unimproved";
  return yearBuilt >= 1836 ? "Commercial" : "Tax Parcel";
}

/**
 * Quantizes a geographic bounding box outward onto a fixed degree grid so micro-pans
 * hit the in-memory LRU cache rather than issuing redundant ArcGIS queries.
 *
 * @param {{ west: number, south: number, east: number, north: number }} bounds
 * @param {number} stepDeg
 * @returns {{ west: number, south: number, east: number, north: number, key: string } | null}
 */
export function quantizeViewportBbox(bounds, stepDeg = 0.004) {
  const w = Number(bounds?.west);
  const s = Number(bounds?.south);
  const e = Number(bounds?.east);
  const n = Number(bounds?.north);
  if (![w, s, e, n].every(Number.isFinite) || e <= w || n <= s) {
    return null;
  }
  const step = Number(stepDeg) > 0 ? Number(stepDeg) : 0.004;
  const qw = Number((Math.floor(w / step) * step).toFixed(5));
  const qs = Number((Math.floor(s / step) * step).toFixed(5));
  const qe = Number((Math.ceil(e / step) * step).toFixed(5));
  const qn = Number((Math.ceil(n / step) * step).toFixed(5));
  return {
    west: qw,
    south: qs,
    east: qe,
    north: qn,
    key: `${qw.toFixed(5)},${qs.toFixed(5)},${qe.toFixed(5)},${qn.toFixed(5)}`,
  };
}

/**
 * Aggregates the oldest valid standing construction year (year_built >= 1836) and
 * preservation attributes across an iterable of building features grouped by 13-digit HCAD ID.
 * Ignores 0/null/pre-1836 sentinel values so modern garages or undated sheds never mask
 * an older historic structure on the same tax parcel.
 *
 * @param {Iterable<object>} buildingFeatures
 * @param {Map<string, object>} [targetMap]
 * @returns {Map<string, { year_built: number, landmark_name: string, historic_district: string, contributing: string, use_category: string, building_count: number }>}
 */
export function collectMinBuildingYearsByHcad(
  buildingFeatures,
  targetMap = new Map(),
  curatedOverrides = null
) {
  if (!buildingFeatures) return targetMap;
  const ovMap =
    curatedOverrides && typeof curatedOverrides === "object"
      ? curatedOverrides.overrides || curatedOverrides
      : null;
  for (const feat of buildingFeatures) {
    const p = feat?.properties;
    if (!p || p.suppress_only) continue;
    const hcad = normalizeHcad13(p.hcad_num || p.HCAD_NUM);
    if (!hcad) continue;
    if (
      ovMap &&
      isEntitySuppressedByOverrides(ovMap, {
        buildingId: p.building_id,
        featureId: p.id,
        hcadNum: hcad,
      })
    ) {
      continue;
    }
    const yr = Number(p.year_built) || 0;
    const validYr = yr >= 1836 && yr <= 2035 ? yr : 0;
    let rec = targetMap.get(hcad);
    if (!rec) {
      rec = {
        year_built: validYr,
        landmark_name: String(p.landmark_name || "").trim(),
        historic_district: String(p.historic_district || "").trim(),
        contributing: String(p.contributing || "").trim(),
        use_category: String(p.use_category || "").trim(),
        building_count: 1,
      };
      targetMap.set(hcad, rec);
    } else {
      rec.building_count += 1;
      if (validYr > 0 && (rec.year_built === 0 || validYr < rec.year_built)) {
        rec.year_built = validYr;
      }
      if (!rec.landmark_name && p.landmark_name) {
        rec.landmark_name = String(p.landmark_name).trim();
      }
      if (!rec.historic_district && p.historic_district) {
        rec.historic_district = String(p.historic_district).trim();
      }
      if (!rec.contributing && p.contributing) {
        rec.contributing = String(p.contributing).trim();
      }
      if (!rec.use_category && p.use_category) {
        rec.use_category = String(p.use_category).trim();
      }
    }
  }
  return targetMap;
}

/**
 * Normalizes and enriches a raw HCAD ArcGIS parcel GeoJSON feature with curated override
 * precedence, minimum nonzero building age, and Pilot Core deduplication.
 *
 * @param {object} rawFeature
 * @param {{
 *   buildingYearsByHcad?: Map<string, object>,
 *   curatedOverrides?: Record<string, object>,
 *   pilotCoreHcads?: Set<string>,
 *   applyOverrideFn?: ((props: object, ovMap: object) => object) | null
 * }} opts
 * @returns {object | null}
 */
export function enrichParcelFeature(
  rawFeature,
  {
    buildingYearsByHcad = new Map(),
    curatedOverrides = {},
    pilotCoreHcads = new Set(),
    applyOverrideFn = null,
  } = {}
) {
  if (!rawFeature || !rawFeature.geometry) return null;
  const rp = rawFeature.properties || {};
  const hcad = normalizeHcad13(rp.HCAD_NUM || rp.hcad_num);
  if (!hcad) return null;
  if (pilotCoreHcads && pilotCoreHcads.has(hcad)) {
    return null;
  }

  const shardInfo = buildingYearsByHcad?.get?.(hcad) || null;
  const ovMap =
    curatedOverrides && typeof curatedOverrides === "object"
      ? curatedOverrides.overrides || curatedOverrides
      : null;
  const parcelOv =
    ovMap && ovMap[hcad] && !ovMap[hcad].is_building_override ? ovMap[hcad] : null;
  const isDemolishedVacantParcel = Boolean(parcelOv?.suppress_only);
  const directOv = !isDemolishedVacantParcel ? parcelOv : null;

  const ovYear = Number(directOv?.year_built) || 0;
  const shardYear = Number(shardInfo?.year_built) || 0;
  const rawYear = Number(rp.year_built || rp.yr_impr) || 0;

  let resolvedYear = 0;
  if (!isDemolishedVacantParcel) {
    if (ovYear >= 1836) {
      resolvedYear = ovYear;
    } else if (shardYear >= 1836) {
      resolvedYear = shardYear;
    } else if (rawYear >= 1836) {
      resolvedYear = rawYear;
    }
  }

  const stateClass = String(rp.state_class || "").trim();
  const address = String(parcelOv?.address || rp.address || "").trim();
  const owner = String(parcelOv?.owner || rp.owner || "").trim();
  const subdivision = String(rp.subdivision || "").trim();
  const useCategory = isDemolishedVacantParcel
    ? "Vacant / Exempt"
    : directOv?.use_category ||
      shardInfo?.use_category ||
      mapHcadStateClassToUseCategory(stateClass, resolvedYear);

  let props = {
    id: `parcel_${hcad}`,
    hcad_num: hcad,
    address,
    owner,
    subdivision,
    state_class: stateClass,
    appr_val: rp.appr_val ?? null,
    year_built: resolvedYear,
    decade:
      resolvedYear >= 1836
        ? resolvedYear < 1850
          ? 1840
          : Math.floor(resolvedYear / 10) * 10
        : 0,
    use_category: useCategory,
    landmark_name: parcelOv?.landmark_name || shardInfo?.landmark_name || "",
    historic_district: parcelOv?.historic_district || shardInfo?.historic_district || "",
    contributing: parcelOv?.contributing || shardInfo?.contributing || "",
    building_count: isDemolishedVacantParcel ? 0 : shardInfo?.building_count || 0,
    ...(isDemolishedVacantParcel ? { is_demolished_vacant: true } : {}),
    is_tax_parcel: true,
    is_dynamic_hcad_parcel: true,
  };

  if (typeof applyOverrideFn === "function" && ovMap) {
    props = applyOverrideFn(props, ovMap);
  }

  return {
    type: "Feature",
    geometry: rawFeature.geometry,
    properties: props,
  };
}

/**
 * Builds a unified, deduplicated GeoJSON FeatureCollection combining Pilot Core parcels
 * and live viewport HCAD parcels enriched with building ages and curated overrides.
 */
export function buildUnifiedParcelCollection({
  pilotFeatures = [],
  dynamicRawFeatures = [],
  buildingYearsByHcad = new Map(),
  curatedOverrides = {},
  applyOverrideFn = null,
} = {}) {
  const pilotCoreHcads = new Set();
  const merged = [];

  for (const pf of pilotFeatures || []) {
    if (!pf || !pf.geometry) continue;
    const hcad = normalizeHcad13(pf.properties?.hcad_num || pf.properties?.HCAD_NUM);
    if (hcad) pilotCoreHcads.add(hcad);
    merged.push(pf);
  }

  const seenDynamic = new Set();
  for (const df of dynamicRawFeatures || []) {
    const hcad = normalizeHcad13(df?.properties?.HCAD_NUM || df?.properties?.hcad_num);
    if (!hcad || seenDynamic.has(hcad)) continue;
    seenDynamic.add(hcad);
    const enriched = enrichParcelFeature(df, {
      buildingYearsByHcad,
      curatedOverrides,
      pilotCoreHcads,
      applyOverrideFn,
    });
    if (enriched) {
      merged.push(enriched);
    }
  }

  return {
    type: "FeatureCollection",
    features: merged,
  };
}

/**
 * Stateful LRU-cached client for querying HCAD's CORS-enabled public_query MapServer
 * with automatic quadtree envelope splitting when dense viewports hit maxRecordCount.
 */
export class HcadViewportParcelClient {
  constructor({
    fetchImpl = typeof fetch === "function" ? fetch.bind(globalThis) : null,
    endpointUrl = HCAD_PUBLIC_QUERY_URL,
    maxCacheEntries = 48,
    maxRecordCount = 2000,
    minFetchZoom = 15.0,
    quantizeStepDeg = 0.004,
    maxSplitDepth = 2,
  } = {}) {
    this.fetchImpl = fetchImpl;
    this.endpointUrl = endpointUrl;
    this.maxCacheEntries = maxCacheEntries;
    this.maxRecordCount = maxRecordCount;
    this.minFetchZoom = minFetchZoom;
    this.quantizeStepDeg = quantizeStepDeg;
    this.maxSplitDepth = maxSplitDepth;
    this.cache = new Map();
    this._activeController = null;
    this._lastRawFeatures = [];
    this._lastViewportKey = "";
  }

  cancelPending() {
    if (this._activeController) {
      this._activeController.abort();
      this._activeController = null;
    }
  }

  getLastRawFeatures() {
    return this._lastRawFeatures;
  }

  _getCached(key) {
    if (!this.cache.has(key)) return null;
    const val = this.cache.get(key);
    this.cache.delete(key);
    this.cache.set(key, val);
    return val;
  }

  _setCached(key, features) {
    if (this.cache.has(key)) {
      this.cache.delete(key);
    } else if (this.cache.size >= this.maxCacheEntries) {
      const oldestKey = this.cache.keys().next().value;
      if (oldestKey !== undefined) this.cache.delete(oldestKey);
    }
    this.cache.set(key, features);
  }

  async _fetchEnvelopeRecursive(bbox, signal, depth = 0) {
    const cached = this._getCached(bbox.key);
    if (cached) return cached;

    if (!this.fetchImpl) return [];
    const params = new URLSearchParams({
      geometry: `${bbox.west},${bbox.south},${bbox.east},${bbox.north}`,
      geometryType: "esriGeometryEnvelope",
      inSR: "4326",
      spatialRel: "esriSpatialRelIntersects",
      outFields: "HCAD_NUM,address,owner,subdivision,state_class,appr_val",
      returnGeometry: "true",
      outSR: "4326",
      f: "geojson",
    });

    const url = `${this.endpointUrl}?${params.toString()}`;
    const res = await this.fetchImpl(url, { signal });
    if (!res || !res.ok) {
      throw new Error(`HCAD parcel query HTTP ${res ? res.status : "error"}`);
    }
    const payload = await res.json();
    const feats = Array.isArray(payload?.features) ? payload.features : [];
    const truncated =
      Boolean(payload?.properties?.exceededTransferLimit) ||
      Boolean(payload?.exceededTransferLimit) ||
      feats.length >= this.maxRecordCount;

    // Split dense inner-loop viewports into 4 sub-envelopes so maxRecordCount never leaves holes
    if (truncated && depth < this.maxSplitDepth) {
      const midLng = Number(((bbox.west + bbox.east) / 2).toFixed(6));
      const midLat = Number(((bbox.south + bbox.north) / 2).toFixed(6));
      if (midLng > bbox.west && midLng < bbox.east && midLat > bbox.south && midLat < bbox.north) {
        const quads = [
          { west: bbox.west, south: midLat, east: midLng, north: bbox.north },
          { west: midLng, south: midLat, east: bbox.east, north: bbox.north },
          { west: bbox.west, south: bbox.south, east: midLng, north: midLat },
          { west: midLng, south: bbox.south, east: bbox.east, north: midLat },
        ].map((q) => ({
          ...q,
          key: `${q.west.toFixed(6)},${q.south.toFixed(6)},${q.east.toFixed(6)},${q.north.toFixed(6)}`,
        }));

        const quadResults = await Promise.all(
          quads.map((q) => this._fetchEnvelopeRecursive(q, signal, depth + 1))
        );
        const deduped = [];
        const seen = new Set();
        for (const list of quadResults) {
          for (const f of list) {
            const hid = normalizeHcad13(f?.properties?.HCAD_NUM || f?.properties?.hcad_num);
            const dedupKey = hid || JSON.stringify(f?.geometry?.coordinates?.[0]?.[0] || []);
            if (seen.has(dedupKey)) continue;
            seen.add(dedupKey);
            deduped.push(f);
          }
        }
        this._setCached(bbox.key, deduped);
        return deduped;
      }
    }

    this._setCached(bbox.key, feats);
    return feats;
  }

  /**
   * Fetches raw HCAD GeoJSON parcel features for a geographic viewport at zoom >= minFetchZoom.
   * Cancels any superseded in-flight viewport query automatically.
   *
   * @param {{ west: number, south: number, east: number, north: number, zoom: number, force?: boolean }} args
   * @returns {Promise<{ updated: boolean, features: object[], fromCache?: boolean }>}
   */
  async fetchForViewport({ west, south, east, north, zoom, force = false } = {}) {
    if (!Number.isFinite(Number(zoom)) || Number(zoom) < this.minFetchZoom) {
      this.cancelPending();
      return { updated: false, features: [] };
    }
    const qbox = quantizeViewportBbox({ west, south, east, north }, this.quantizeStepDeg);
    if (!qbox) {
      return { updated: false, features: this._lastRawFeatures };
    }
    if (!force && qbox.key === this._lastViewportKey && this.cache.has(qbox.key)) {
      this._lastRawFeatures = this._getCached(qbox.key) || [];
      return { updated: false, features: this._lastRawFeatures, fromCache: true };
    }

    const cached = this._getCached(qbox.key);
    if (cached) {
      this.cancelPending();
      this._lastViewportKey = qbox.key;
      this._lastRawFeatures = cached;
      return { updated: true, features: cached, fromCache: true };
    }

    this.cancelPending();
    const controller = new AbortController();
    this._activeController = controller;
    try {
      const features = await this._fetchEnvelopeRecursive(qbox, controller.signal, 0);
      if (this._activeController === controller) {
        this._activeController = null;
      }
      this._lastViewportKey = qbox.key;
      this._lastRawFeatures = features;
      return { updated: true, features, fromCache: false };
    } catch (err) {
      if (this._activeController === controller) {
        this._activeController = null;
      }
      if (err && (err.name === "AbortError" || String(err.message || "").includes("aborted"))) {
        return { updated: false, features: this._lastRawFeatures, aborted: true };
      }
      throw err;
    }
  }
}

const HCAD_SEARCH_DIRECTIONAL_MAP = {
  NORTH: "N",
  SOUTH: "S",
  EAST: "E",
  WEST: "W",
  N: "N",
  S: "S",
  E: "E",
  W: "W",
};

const HCAD_SEARCH_SUFFIX_MAP = {
  STREET: "ST",
  AVENUE: "AVE",
  BOULEVARD: "BLVD",
  DRIVE: "DR",
  ROAD: "RD",
  LANE: "LN",
  COURT: "CT",
  PLACE: "PL",
  PARKWAY: "PKWY",
  FREEWAY: "FWY",
  HIGHWAY: "HWY",
  CIRCLE: "CIR",
  TRAIL: "TRL",
  TERRACE: "TER",
  SPEEDWAY: "SPWY",
  SQUARE: "SQ",
};

/**
 * Compute a representative `[lng, lat]` centroid from a GeoJSON geometry (`Point`, `Polygon`,
 * or `MultiPolygon`). Returns `null` when coordinates are missing or non-finite.
 */
export function computeParcelCentroid(geometry) {
  return computeGeoJsonCentroid(geometry);
}

/**
 * Normalize a user street-address search query into a sanitized ArcGIS SQL `WHERE` clause
 * against HCAD `public_query/MapServer/0` (`address LIKE '...'`).
 *
 * Returns `null` when the query does not contain a concrete house number (>= 1, non-zero-led)
 * plus at least 2 characters of the primary street name token (ignoring bare leading directionals
 * like `"3346 S"` so live queries never match overly broad prefixes while the user is mid-keystroke).
 *
 * Automatically includes HCAD's double-space directional variant (`"10305 S  MAIN%"`) alongside
 * single-space (`"10305 S MAIN%"`) whenever a leading `N`/`S`/`E`/`W` token is present.
 */
export function buildHcadAddressPrefixWhere(rawQuery) {
  let s = String(rawQuery || "").trim();
  if (!s) return null;

  // Strip trailing Houston / TX / ZIP boilerplate and apartment/suite tails.
  s = s.replace(/,?\s+houston(?:\s*,?\s*(?:tx|texas))?(?:\s+\d{5}(?:-\d{4})?)?$/i, " ");
  s = s.replace(/\s+(?:#|apt\.?|bldg\.?|ste\.?|suite|unit)\s*[a-z0-9-]*$/i, " ");
  s = s
    .toUpperCase()
    .replace(/[.,'"`\\;%_()[\]{}]/g, " ")
    .replace(/\s+/g, " ")
    .trim();

  const m = s.match(/^(\d+[A-Z]?)\s+([A-Z0-9][A-Z0-9\s-]*)$/);
  if (!m) return null;

  const houseNum = m[1];
  const houseDigits = houseNum.replace(/\D/g, "");
  if (!houseDigits || houseDigits.startsWith("0")) return null;

  const rawTokens = m[2]
    .split(/\s+/)
    .map((tok) => tok.replace(/[^A-Z0-9-]/g, ""))
    .filter(Boolean);
  if (rawTokens.length === 0) return null;

  const tokens = rawTokens.slice();
  if (HCAD_SEARCH_DIRECTIONAL_MAP[tokens[0]] && tokens.length >= 2) {
    tokens[0] = HCAD_SEARCH_DIRECTIONAL_MAP[tokens[0]];
  }
  if (tokens.length >= 2) {
    const lastIdx = tokens.length - 1;
    if (HCAD_SEARCH_SUFFIX_MAP[tokens[lastIdx]]) {
      tokens[lastIdx] = HCAD_SEARCH_SUFFIX_MAP[tokens[lastIdx]];
    }
  }

  const isLeadingDir =
    tokens.length >= 1 && ["N", "S", "E", "W"].includes(tokens[0]);
  if (isLeadingDir && tokens.length < 2) {
    return null;
  }
  const primaryStreetToken = isLeadingDir ? tokens[1] : tokens[0];
  if (!primaryStreetToken || primaryStreetToken.length < 2) {
    return null;
  }

  const streetPrefix = tokens.join(" ");
  const prefixes = [`${houseNum} ${streetPrefix}`];
  if (isLeadingDir) {
    const rest = tokens.slice(1).join(" ");
    prefixes.push(`${houseNum} ${tokens[0]}  ${rest}`);
  }

  const clauses = prefixes.map((p) => `address LIKE '${p}%'`);
  const where = clauses.length === 1 ? clauses[0] : `(${clauses.join(" OR ")})`;

  return {
    houseNum,
    streetPrefix,
    streetTokens: tokens,
    prefixes,
    where,
  };
}

/**
 * Filter and rank already-fetched or newly queried HCAD parcel GeoJSON features against a user
 * street-address search query (`F2-10` / `DF-4`). Used both synchronously against the active
 * viewport parcel cache (`0ms` fast path) and on live REST query results.
 */
export function matchHcadParcelFeaturesByAddressQuery(
  features,
  rawQuery,
  {
    knownAtlasHcads = new Set(),
    minBuildingYearsByHcad = new Map(),
    resolveContextAtPoint = null,
  } = {}
) {
  const spec = buildHcadAddressPrefixWhere(rawQuery);
  if (!spec || !Array.isArray(features) || features.length === 0) return [];

  const matched = [];
  const seenHcad = new Set();

  for (const rawFeat of features) {
    if (!rawFeat || typeof rawFeat !== "object") continue;
    const rawProps = rawFeat.properties || rawFeat.attributes || {};
    const hcad = normalizeHcad13(rawProps.HCAD_NUM ?? rawProps.hcad_num ?? "");
    if (hcad.length !== 13 || seenHcad.has(hcad)) continue;

    const cleanAddr = String(rawProps.address || "")
      .toUpperCase()
      .replace(/[.,]/g, " ")
      .replace(/\s+/g, " ")
      .trim();
    if (!cleanAddr || /^0(?:\s|$)/.test(cleanAddr)) continue;

    const addrParts = cleanAddr.split(" ");
    const addrNum = addrParts[0] || "";
    const exactNum = addrNum === spec.houseNum;
    const prefixNum = !exactNum && addrNum.startsWith(spec.houseNum);
    if (!exactNum && !prefixNum) continue;

    const addrStreet = addrParts.slice(1).join(" ");
    if (!spec.streetTokens.every((tok) => addrStreet.includes(tok))) continue;

    const normGeom =
      rawFeat.geometry &&
      !rawFeat.geometry.type &&
      Array.isArray(rawFeat.geometry.rings) &&
      rawFeat.geometry.rings.length > 0
        ? { type: "Polygon", coordinates: rawFeat.geometry.rings }
        : rawFeat.geometry || null;

    const centroid = computeParcelCentroid(normGeom);
    const extraCtx =
      centroid && typeof resolveContextAtPoint === "function"
        ? resolveContextAtPoint(centroid[0], centroid[1]) || {}
        : {};
    let enriched = null;
    if (rawProps.is_live_hcad_parcel && rawProps.use_category) {
      enriched = {
        type: "Feature",
        geometry: normGeom,
        properties: { ...extraCtx, ...rawProps, hcad_num: hcad, address: cleanAddr },
      };
    } else {
      const baseEnriched = enrichParcelFeature(
        { type: "Feature", geometry: normGeom, properties: rawProps },
        {
          buildingYearsByHcad: minBuildingYearsByHcad,
          pilotCoreHcads: knownAtlasHcads,
        }
      );
      if (baseEnriched) {
        enriched = {
          type: "Feature",
          geometry: baseEnriched.geometry,
          properties: {
            ...extraCtx,
            ...baseEnriched.properties,
            address: cleanAddr,
            is_live_hcad_parcel: true,
          },
        };
      }
    }
    if (!enriched) continue;

    seenHcad.add(hcad);
    matched.push({
      feature: enriched,
      hcadNum: hcad,
      address: enriched.properties.address || cleanAddr,
      exactHouseNum: exactNum,
      exactStreetPrefix:
        addrStreet === spec.streetPrefix || addrStreet.startsWith(`${spec.streetPrefix} `),
      centroid,
      lon: centroid ? centroid[0] : null,
      lat: centroid ? centroid[1] : null,
    });
  }

  matched.sort((a, b) => {
    if (a.exactHouseNum !== b.exactHouseNum) return a.exactHouseNum ? -1 : 1;
    if (a.exactStreetPrefix !== b.exactStreetPrefix) return a.exactStreetPrefix ? -1 : 1;
    return a.address.localeCompare(b.address) || a.hcadNum.localeCompare(b.hcadNum);
  });

  return matched;
}

/**
 * Query HCAD's live countywide `public_query/MapServer/0` parcel layer by street-address prefix
 * so vacant/unimproved parcels missing from `search_index.json` and `address_shards/` (such as
 * `3346 Scott St` and `3348 Scott St`, `F2-10`) resolve cleanly in the Atlas search dropdown
 * and `#q=` deep links with polygon geometry (`DF-4`).
 */
export async function searchHcadParcelsByAddressPrefix(
  rawQuery,
  {
    fetchImpl = globalThis.fetch ? globalThis.fetch.bind(globalThis) : null,
    signal = undefined,
    baseUrl = HCAD_PUBLIC_QUERY_URL,
    resultRecordCount = 12,
    knownAtlasHcads = new Set(),
    minBuildingYearsByHcad = new Map(),
    resolveContextAtPoint = null,
  } = {}
) {
  const spec = buildHcadAddressPrefixWhere(rawQuery);
  if (!spec || typeof fetchImpl !== "function") return [];

  const params = new URLSearchParams({
    where: spec.where,
    outFields:
      "HCAD_NUM,address,owner,subdivision,state_class,impr_val,land_val,mkt_val,appr_val",
    returnGeometry: "true",
    outSR: "4326",
    resultRecordCount: String(Math.max(1, Math.min(25, Number(resultRecordCount) || 12))),
    f: "geojson",
  });

  const cleanBase = String(baseUrl || HCAD_PUBLIC_QUERY_URL).replace(/\/+$/, "");
  const queryEndpoint = cleanBase.endsWith("/query") ? cleanBase : `${cleanBase}/query`;
  const resp = await fetchImpl(`${queryEndpoint}?${params.toString()}`, { signal });
  if (!resp || !resp.ok) return [];
  const data = await resp.json();
  if (!data || data.error || !Array.isArray(data.features)) return [];

  return matchHcadParcelFeaturesByAddressQuery(data.features, rawQuery, {
    knownAtlasHcads,
    minBuildingYearsByHcad,
    resolveContextAtPoint,
  });
}
