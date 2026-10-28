/**
 * MapLibre-agnostic HCAD countywide viewport parcel client, LRU bbox cache,
 * quadtree transfer-limit splitter, Pilot Core deduplicator, and multi-building
 * minimum-age enricher for The Houston Building Atlas.
 */

import { isEntitySuppressedByOverrides } from "./curatedEdits.js?v=20261028b";

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
    const validYr = yr >= 1836 && yr <= 2030 ? yr : 0;
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
