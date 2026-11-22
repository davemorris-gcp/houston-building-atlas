/**
 * Shared Spatial Context Thumbnail & Hover/Tap Houston 610 Loop Locator Engine.
 *
 * Renders the compact "Option C + Hover/Tap Option B" parcel/footprint locator across:
 *   1. Plat & Variance Monitor (`plat-monitor.html` PlatTracker GIS boundary box)
 *   2. 15-Point Property Watchlist (`plat-monitor.html?tab=watchlist` property header)
 *   3. Main Atlas Active Permit & Demolition Watchlist Modal (`#permit-watch-modal`)
 *   4. Main Atlas Inspector Drawer Active Permit Alert Card (`#inspector-permit-alert-card`)
 *
 * Imports cleanly in bare Node.js with zero top-level DOM globals (`SA-1` / `UT-12`).
 */

export const ARCGIS_WORLD_STREET_EXPORT_URL =
  "https://services.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/export";

export const HOUSTON_DOWNTOWN_ANCHOR = Object.freeze({
  lng: -95.3655,
  lat: 29.7595,
  label: "Downtown",
});

const WEB_MERCATOR_HALF_CIRCUMFERENCE_M = 20037508.342789244;
const DEG_TO_RAD = Math.PI / 180;
const RAD_TO_DEG = 180 / Math.PI;
const FEET_PER_METER = 3.280839895;

// Authentic simplified WGS84 polylines for Houston's orienting loops, radials, and bayous
const HOUSTON_I610_RING = Object.freeze([
  [-95.456, 29.806],
  [-95.388, 29.814],
  [-95.325, 29.813],
  [-95.286, 29.807],
  [-95.269, 29.775],
  [-95.265, 29.724],
  [-95.282, 29.690],
  [-95.332, 29.679],
  [-95.398, 29.678],
  [-95.455, 29.680],
  [-95.462, 29.715],
  [-95.461, 29.776],
  [-95.456, 29.806],
]);

const HOUSTON_BELTWAY8_RING = Object.freeze([
  [-95.558, 29.932],
  [-95.415, 29.942],
  [-95.252, 29.936],
  [-95.168, 29.858],
  [-95.158, 29.730],
  [-95.182, 29.615],
  [-95.325, 29.596],
  [-95.485, 29.602],
  [-95.562, 29.655],
  [-95.564, 29.815],
  [-95.558, 29.932],
]);

const HOUSTON_FREEWAY_CORRIDORS = Object.freeze([
  {
    id: "I-10",
    pts: [
      [-95.68, 29.784],
      [-95.461, 29.779],
      [-95.368, 29.769],
      [-95.268, 29.774],
      [-95.12, 29.778],
    ],
  },
  {
    id: "I-45",
    pts: [
      [-95.425, 29.98],
      [-95.388, 29.814],
      [-95.371, 29.764],
      [-95.348, 29.738],
      [-95.282, 29.690],
      [-95.21, 29.60],
    ],
  },
  {
    id: "I-69",
    pts: [
      [-95.58, 29.65],
      [-95.462, 29.731],
      [-95.392, 29.732],
      [-95.358, 29.756],
      [-95.335, 29.813],
      [-95.27, 29.95],
    ],
  },
  {
    id: "288",
    pts: [
      [-95.362, 29.749],
      [-95.377, 29.718],
      [-95.382, 29.679],
      [-95.388, 29.58],
    ],
  },
  {
    id: "290",
    pts: [
      [-95.456, 29.806],
      [-95.56, 29.87],
      [-95.66, 29.93],
    ],
  },
]);

const HOUSTON_BAYOU_TRACES = Object.freeze([
  // Buffalo Bayou
  [
    [-95.62, 29.763],
    [-95.462, 29.764],
    [-95.405, 29.762],
    [-95.363, 29.762],
    [-95.295, 29.754],
    [-95.20, 29.742],
  ],
  // Brays Bayou
  [
    [-95.58, 29.692],
    [-95.458, 29.696],
    [-95.392, 29.706],
    [-95.335, 29.718],
    [-95.288, 29.726],
  ],
  // White Oak Bayou
  [
    [-95.52, 29.865],
    [-95.425, 29.812],
    [-95.386, 29.784],
    [-95.363, 29.765],
  ],
]);

const COMPASS_16 = Object.freeze([
  "N",
  "NNE",
  "NE",
  "ENE",
  "E",
  "ESE",
  "SE",
  "SSE",
  "S",
  "SSW",
  "SW",
  "WSW",
  "W",
  "WNW",
  "NW",
  "NNW",
]);

function sanitizeSpatialText(raw) {
  return String(raw ?? "").replace(/[\u2010\u2013\u2014]/g, "-");
}

function escHtml(raw) {
  return sanitizeSpatialText(raw)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function isValidHoustonCoord(lng, lat) {
  return (
    Number.isFinite(lng) &&
    Number.isFinite(lat) &&
    lng >= -96.2 &&
    lng <= -94.7 &&
    lat >= 29.3 &&
    lat <= 30.3
  );
}

export function lngLatToMercatorMeters(lng, lat) {
  const clampedLat = Math.max(-85.05112878, Math.min(85.05112878, Number(lat)));
  const x = (Number(lng) * WEB_MERCATOR_HALF_CIRCUMFERENCE_M) / 180;
  const y =
    (Math.log(Math.tan((90 + clampedLat) * (Math.PI / 360))) *
      WEB_MERCATOR_HALF_CIRCUMFERENCE_M) /
    Math.PI;
  return [x, y];
}

export function mercatorMetersToLngLat(mx, my) {
  const lng = (Number(mx) * 180) / WEB_MERCATOR_HALF_CIRCUMFERENCE_M;
  const lat =
    (Math.atan(Math.exp((Number(my) * Math.PI) / WEB_MERCATOR_HALF_CIRCUMFERENCE_M)) * 360) /
      Math.PI -
    90;
  return [lng, lat];
}

function pointInRing(lng, lat, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const xi = ring[i][0];
    const yi = ring[i][1];
    const xj = ring[j][0];
    const yj = ring[j][1];
    const intersect =
      yi > lat !== yj > lat && lng < ((xj - xi) * (lat - yi)) / (yj - yi || 1e-12) + xi;
    if (intersect) inside = !inside;
  }
  return inside;
}

/**
 * Normalizes flexible polygon inputs into an array of coordinate rings `[[[lng, lat], ...], ...]`:
 *   - GeoJSON Geometry (`Polygon` or `MultiPolygon`)
 *   - GeoJSON Feature
 *   - Single ring `[[lng, lat], ...]`
 *   - Array of rings `[[[lng, lat], ...], ...]`
 */
export function normalizePolygonRingsInput(rawGeomOrRings) {
  if (!rawGeomOrRings) return [];
  const geom =
    rawGeomOrRings.type === "Feature" && rawGeomOrRings.geometry
      ? rawGeomOrRings.geometry
      : rawGeomOrRings;

  if (geom && typeof geom === "object" && !Array.isArray(geom)) {
    if (geom.type === "Polygon" && Array.isArray(geom.coordinates)) {
      return normalizePolygonRingsInput(geom.coordinates);
    }
    if (geom.type === "MultiPolygon" && Array.isArray(geom.coordinates)) {
      const out = [];
      for (const poly of geom.coordinates) {
        if (Array.isArray(poly)) {
          for (const ring of normalizePolygonRingsInput(poly)) {
            out.push(ring);
          }
        }
      }
      return out;
    }
    return [];
  }

  if (!Array.isArray(geom) || geom.length === 0) return [];

  // Detect single ring `[[lng, lat], ...]` vs multi-ring `[[[lng, lat], ...], ...]`
  const first = geom[0];
  if (Array.isArray(first) && typeof first[0] === "number" && typeof first[1] === "number") {
    const clean = geom.filter(
      (pt) => Array.isArray(pt) && isValidHoustonCoord(Number(pt[0]), Number(pt[1]))
    );
    return clean.length >= 3 ? [clean.map((pt) => [Number(pt[0]), Number(pt[1])])] : [];
  }

  const rings = [];
  for (const r of geom) {
    if (!Array.isArray(r)) continue;
    const clean = r.filter(
      (pt) => Array.isArray(pt) && isValidHoustonCoord(Number(pt[0]), Number(pt[1]))
    );
    if (clean.length >= 3) {
      rings.push(clean.map((pt) => [Number(pt[0]), Number(pt[1])]));
    }
  }
  return rings;
}

/**
 * Computes distance, compass bearing, and loop ring context relative to Downtown Houston.
 */
export function computeHoustonDowntownContext(lat, lng) {
  const cleanLat = Number(lat);
  const cleanLng = Number(lng);
  if (!isValidHoustonCoord(cleanLng, cleanLat)) {
    return null;
  }
  const cosLat = Math.cos(((cleanLat + HOUSTON_DOWNTOWN_ANCHOR.lat) * 0.5) * DEG_TO_RAD);
  const dxMiles = (cleanLng - HOUSTON_DOWNTOWN_ANCHOR.lng) * cosLat * 69.172;
  const dyMiles = (cleanLat - HOUSTON_DOWNTOWN_ANCHOR.lat) * 68.88;
  const distanceMiles = Math.hypot(dxMiles, dyMiles);

  const angleRad = Math.atan2(dxMiles, dyMiles); // 0 = North, +pi/2 = East
  const deg = ((angleRad * RAD_TO_DEG) % 360 + 360) % 360;
  const compass = COMPASS_16[Math.round(deg / 22.5) % 16];

  const inside610 = pointInRing(cleanLng, cleanLat, HOUSTON_I610_RING);
  const insideBeltway8 = inside610 || pointInRing(cleanLng, cleanLat, HOUSTON_BELTWAY8_RING);

  let loopLabel = "Outside Beltway 8";
  if (distanceMiles < 0.85) {
    loopLabel = "Downtown Core";
  } else if (inside610) {
    loopLabel = "Inside 610 Loop";
  } else if (insideBeltway8) {
    loopLabel = "Between 610 & Beltway 8";
  }

  const distText =
    distanceMiles < 0.35
      ? "In Downtown Houston"
      : `${distanceMiles.toFixed(1)} mi ${compass} of Downtown`;

  return {
    distanceMiles: Number(distanceMiles.toFixed(2)),
    compass,
    inside610,
    insideBeltway8,
    loopLabel,
    distText,
    summaryText: distanceMiles < 0.35 ? "Downtown Core" : `${distText} (${loopLabel})`,
  };
}

/**
 * Computes an aspect-locked Web Mercator block viewport around `(lat, lng)` and optional polygon rings,
 * returning both the keyless ArcGIS `World_Street_Map` export URL and projected SVG path/scale geometry.
 */
export function buildBlockViewportProjection({
  lat,
  lng,
  polygonRings = [],
  width = 164,
  height = 104,
  minWidthMeters = 240,
} = {}) {
  const rings = normalizePolygonRingsInput(polygonRings);
  let cLng = Number(lng);
  let cLat = Number(lat);

  if (!isValidHoustonCoord(cLng, cLat) && rings.length > 0) {
    let sx = 0;
    let sy = 0;
    let n = 0;
    for (const r of rings) {
      for (const [px, py] of r) {
        sx += px;
        sy += py;
        n += 1;
      }
    }
    if (n > 0) {
      cLng = sx / n;
      cLat = sy / n;
    }
  }
  if (!isValidHoustonCoord(cLng, cLat)) {
    return null;
  }

  let minMx = Infinity;
  let maxMx = -Infinity;
  let minMy = Infinity;
  let maxMy = -Infinity;

  if (rings.length > 0) {
    for (const r of rings) {
      for (const [px, py] of r) {
        const [mx, my] = lngLatToMercatorMeters(px, py);
        if (mx < minMx) minMx = mx;
        if (mx > maxMx) maxMx = mx;
        if (my < minMy) minMy = my;
        if (my > maxMy) maxMy = my;
      }
    }
  } else {
    const [cx, cy] = lngLatToMercatorMeters(cLng, cLat);
    minMx = maxMx = cx;
    minMy = maxMy = cy;
  }

  const midMx = (minMx + maxMx) * 0.5;
  const midMy = (minMy + maxMy) * 0.5;
  const aspect = width / height;

  // Pad polygon span by 2.35x (with a ~240m minimum block width so adjacent streets & cross-streets frame the parcel)
  const polySpanX = Math.max(maxMx - minMx, 1);
  const polySpanY = Math.max(maxMy - minMy, 1);
  let spanX = Math.max(minWidthMeters, polySpanX * 2.35, polySpanY * 2.35 * aspect);
  let spanY = spanX / aspect;

  const boxMinMx = midMx - spanX * 0.5;
  const boxMaxMx = midMx + spanX * 0.5;
  const boxMinMy = midMy - spanY * 0.5;
  const boxMaxMy = midMy + spanY * 0.5;

  const [minLng, minLat] = mercatorMetersToLngLat(boxMinMx, boxMinMy);
  const [maxLng, maxLat] = mercatorMetersToLngLat(boxMaxMx, boxMaxMy);

  const imgW = Math.round(width * 2);
  const imgH = Math.round(height * 2);
  const params = new URLSearchParams({
    bbox: `${minLng.toFixed(6)},${minLat.toFixed(6)},${maxLng.toFixed(6)},${maxLat.toFixed(6)}`,
    bboxSR: "4326",
    imageSR: "3857",
    size: `${imgW},${imgH}`,
    dpi: "144",
    format: "png32",
    f: "image",
  });
  const streetMapUrl = `${ARCGIS_WORLD_STREET_EXPORT_URL}?${params.toString()}`;

  const projectPt = (ptLng, ptLat) => {
    const [mx, my] = lngLatToMercatorMeters(ptLng, ptLat);
    const px = ((mx - boxMinMx) / spanX) * width;
    const py = ((boxMaxMy - my) / spanY) * height;
    return [px, py];
  };

  const svgPathData = rings
    .map((ring) =>
      ring
        .map(([px, py], idx) => {
          const [sx, sy] = projectPt(px, py);
          return `${idx === 0 ? "M" : "L"}${sx.toFixed(1)},${sy.toFixed(1)}`;
        })
        .join(" ") + " Z"
    )
    .join(" ");

  const [dotX, dotY] = projectPt(cLng, cLat);

  // True ground meters-per-pixel at Houston latitude (Mercator sec(lat) distortion correction)
  const cosLat = Math.cos(cLat * DEG_TO_RAD) || 1;
  const groundMetersPerPx = (spanX * cosLat) / width;
  const groundFeetPerPx = groundMetersPerPx * FEET_PER_METER;
  const candidateBarFeet = [50, 100, 200, 500, 1000];
  let chosenFeet = 100;
  for (const f of candidateBarFeet) {
    const pxLen = f / groundFeetPerPx;
    chosenFeet = f;
    if (pxLen >= 18 && pxLen <= 44) break;
  }
  const scaleBarPx = Math.max(14, Math.min(48, chosenFeet / groundFeetPerPx));
  const scaleLabel = chosenFeet >= 1000 ? `${(chosenFeet / 5280).toFixed(2)} mi` : `${chosenFeet} ft`;

  return {
    centerLng: cLng,
    centerLat: cLat,
    streetMapUrl,
    svgPathData,
    hasPolygon: rings.length > 0,
    dotX: Number(dotX.toFixed(1)),
    dotY: Number(dotY.toFixed(1)),
    scaleBarPx: Number(scaleBarPx.toFixed(1)),
    scaleLabel,
  };
}

export function cleanHistoricDistrictLabel(rawDistrict) {
  const clean = sanitizeSpatialText(rawDistrict).trim();
  if (!clean) return "";
  const low = clean.toLowerCase();
  if (low === "outside historic district" || low === "outside city district") {
    return "";
  }
  return clean;
}

/**
 * Resolves a simplified neighborhood or historic district ring from `overlaysData` if loaded.
 */
export function findContextZoneRingFromOverlays(overlaysData, { historicDistrict = "", neighborhood = "", lng = null, lat = null } = {}) {
  if (!overlaysData || typeof overlaysData !== "object") return null;
  const cleanHd = cleanHistoricDistrictLabel(historicDistrict).toLowerCase();
  const cleanNb = sanitizeSpatialText(neighborhood).trim().toLowerCase();

  const buildSimplifiedHit = (labelRaw, rings) => {
    const simplifiedRings = [];
    for (const ring of rings) {
      const step = Math.max(1, Math.floor(ring.length / 28));
      const simplified = ring.filter((_pt, idx) => idx % step === 0);
      if (simplified.length >= 3) {
        simplifiedRings.push(simplified);
      }
    }
    if (!simplifiedRings.length) return null;
    return {
      label: sanitizeSpatialText(labelRaw).trim(),
      ring: simplifiedRings[0],
      rings: simplifiedRings,
    };
  };

  const tryCollection = (fc, matchName) => {
    const feats = fc?.features;
    if (!Array.isArray(feats) || !feats.length) return null;
    if (matchName) {
      // Pass 1: exact normalized match before substring fallback
      for (const f of feats) {
        const p = f?.properties || {};
        const cand = sanitizeSpatialText(p.name || p.district_name || p.raw_name || "").trim();
        if (!cand || cand.toLowerCase() !== matchName) continue;
        const hit = buildSimplifiedHit(cand, normalizePolygonRingsInput(f.geometry));
        if (hit) return hit;
      }
      // Pass 2: substring fallback
      for (const f of feats) {
        const p = f?.properties || {};
        const cand = sanitizeSpatialText(p.name || p.district_name || p.raw_name || "").trim();
        if (!cand) continue;
        const low = cand.toLowerCase();
        if (low.includes(matchName) || matchName.includes(low)) {
          const hit = buildSimplifiedHit(cand, normalizePolygonRingsInput(f.geometry));
          if (hit) return hit;
        }
      }
    }
    if (isValidHoustonCoord(Number(lng), Number(lat))) {
      for (const f of feats) {
        const rings = normalizePolygonRingsInput(f.geometry);
        if (rings.some((r) => pointInRing(Number(lng), Number(lat), r))) {
          const p = f?.properties || {};
          const cand = sanitizeSpatialText(p.name || p.district_name || p.raw_name || "").trim();
          const hit = buildSimplifiedHit(cand, rings);
          if (hit) return hit;
        }
      }
    }
    return null;
  };

  if (cleanHd) {
    const hdHit = tryCollection(overlaysData.historic_districts, cleanHd);
    if (hdHit) return hdHit;
  }
  if (cleanNb) {
    const snHit = tryCollection(overlaysData.super_neighborhoods, cleanNb);
    if (snHit) return snHit;
  }
  if (isValidHoustonCoord(Number(lng), Number(lat))) {
    return (
      tryCollection(overlaysData.historic_districts, "") ||
      tryCollection(overlaysData.super_neighborhoods, "")
    );
  }
  return null;
}

/**
 * Builds the pure-SVG Houston 610 Loop (+ Beltway 8 when outer-ring) city locator map,
 * supporting both the miniature corner badge (`mini: true`) and the detailed Option B popover (`mini: false`).
 */
export function buildHoustonLoopLocatorSvg({
  lat,
  lng,
  width = 232,
  height = 142,
  mini = false,
  zoneLabel = "",
  zoneRing = null,
} = {}) {
  const cLat = Number(lat);
  const cLng = Number(lng);
  if (!isValidHoustonCoord(cLng, cLat)) return "";

  const vbW = 232;
  const vbH = 142;

  // Inner-loop frame covers all of 610 + Heights + Third Ward + Montrose + Bellaire + Oak Forest;
  // automatically zooms out to Beltway 8 for outer Harris County properties so the beacon never clips.
  const fitsInnerFrame =
    cLng >= -95.505 && cLng <= -95.225 && cLat >= 29.655 && cLat <= 29.838;

  let minLng = fitsInnerFrame ? -95.525 : Math.min(-95.60, cLng - 0.05);
  let maxLng = fitsInnerFrame ? -95.205 : Math.max(-95.13, cLng + 0.05);
  let minLat = fitsInnerFrame ? 29.648 : Math.min(29.56, cLat - 0.04);
  let maxLat = fitsInnerFrame ? 29.848 : Math.max(29.97, cLat + 0.04);

  const midLat = (minLat + maxLat) * 0.5;
  const cosLat = Math.cos(midLat * DEG_TO_RAD) || 1;
  let spanX = (maxLng - minLng) * cosLat;
  let spanY = maxLat - minLat;
  const targetAspect = vbW / vbH;
  if (spanX / spanY > targetAspect) {
    const newSpanY = spanX / targetAspect;
    const midY = (minLat + maxLat) * 0.5;
    minLat = midY - newSpanY * 0.5;
    maxLat = midY + newSpanY * 0.5;
    spanY = newSpanY;
  } else {
    const newSpanX = spanY * targetAspect;
    const midX = (minLng + maxLng) * 0.5;
    minLng = midX - (newSpanX / cosLat) * 0.5;
    maxLng = midX + (newSpanX / cosLat) * 0.5;
    spanX = newSpanX;
  }

  const proj = ([x, y]) => {
    const px = (((x - minLng) * cosLat) / spanX) * vbW;
    const py = ((maxLat - y) / spanY) * vbH;
    return [px, py];
  };

  const toPolylineAttr = (pts) =>
    pts
      .map((p) => {
        const [px, py] = proj(p);
        return `${px.toFixed(1)},${py.toFixed(1)}`;
      })
      .join(" ");

  const [dtX, dtY] = proj([HOUSTON_DOWNTOWN_ANCHOR.lng, HOUSTON_DOWNTOWN_ANCHOR.lat]);
  const [dotX, dotY] = proj([cLng, cLat]);
  const [loopLabelX, loopLabelY] = proj([-95.282, 29.688]);
  const [bwLabelX, bwLabelY] = proj([-95.182, 29.615]);

  let zoneSvg = "";
  const normZoneRings = zoneRing ? normalizePolygonRingsInput(zoneRing) : [];
  if (normZoneRings.length > 0) {
    zoneSvg = normZoneRings
      .map(
        (r) =>
          `<polygon points="${toPolylineAttr(r)}" fill="rgba(196,61,47,0.18)" stroke="#c43d2f" stroke-width="${mini ? 1.6 : 1.2}"/>`
      )
      .join("");
  } else {
    zoneSvg = `<circle cx="${dotX.toFixed(1)}" cy="${dotY.toFixed(1)}" r="${mini ? 13 : 11}" fill="rgba(196,61,47,0.14)" stroke="rgba(196,61,47,0.45)" stroke-width="1" stroke-dasharray="2.5,2"/>`;
  }

  const shortZone = sanitizeSpatialText(zoneLabel)
    .replace(/\bHistoric District\b/gi, "HD")
    .replace(/\bSuper Neighborhood\b/gi, "")
    .trim()
    .slice(0, 22)
    .toUpperCase();

  // Place zone label and DOWNTOWN label on opposite sides so they never collide
  const dotIsNorth = dotY < dtY - 3;
  const zLblY = Math.max(14, Math.min(vbH - 8, dotIsNorth ? dotY - 10 : dotY + 15));
  const zLblX = Math.max(38, Math.min(vbW - 38, dotX));
  const dtLblY = dotIsNorth ? dtY + 13 : dtY - 6;

  const bayouLines = HOUSTON_BAYOU_TRACES.map(
    (pts) =>
      `<polyline points="${toPolylineAttr(pts)}" fill="none" stroke="#93c5fd" stroke-width="${mini ? 2.4 : 1.6}" stroke-linecap="round" stroke-linejoin="round" opacity="0.85"/>`
  ).join("");

  const freewayLines = HOUSTON_FREEWAY_CORRIDORS.map(
    (fw) =>
      `<polyline points="${toPolylineAttr(fw.pts)}" fill="none" stroke="#cbd5e1" stroke-width="${mini ? 2.2 : 1.5}" stroke-linecap="round" stroke-linejoin="round"/>`
  ).join("");

  const beltwaySvg = !fitsInnerFrame
    ? `<polygon points="${toPolylineAttr(HOUSTON_BELTWAY8_RING)}" fill="rgba(255,255,255,0.28)" stroke="#94a3b8" stroke-width="${mini ? 2.0 : 1.4}" stroke-dasharray="4,2"/>`
    : "";

  const freewayBadges = mini
    ? ""
    : (() => {
        const i10Pt = proj([-95.492, 29.781]);
        const i45Pt = proj([-95.394, 29.832]);
        const sh288Pt = proj([-95.382, 29.662]);
        return `
          <text x="${Math.max(14, i10Pt[0]).toFixed(1)}" y="${(i10Pt[1] - 3).toFixed(1)}" font-family="system-ui, sans-serif" font-size="8.5" font-weight="700" fill="#64748b">I-10</text>
          <text x="${i45Pt[0].toFixed(1)}" y="${Math.max(11, i45Pt[1]).toFixed(1)}" text-anchor="middle" font-family="system-ui, sans-serif" font-size="8.5" font-weight="700" fill="#64748b">I-45</text>
          <text x="${(sh288Pt[0] + 11).toFixed(1)}" y="${Math.min(vbH - 5, sh288Pt[1]).toFixed(1)}" font-family="system-ui, sans-serif" font-size="8.5" font-weight="700" fill="#64748b">288</text>
        `;
      })();

  return `<svg width="${width}" height="${height}" viewBox="0 0 ${vbW} ${vbH}" role="img" aria-label="Houston 610 Loop location map">
    <rect width="${vbW}" height="${vbH}" fill="#eef4f8"/>
    ${bayouLines}
    ${freewayLines}
    ${beltwaySvg}
    <polygon points="${toPolylineAttr(HOUSTON_I610_RING)}" fill="rgba(255,255,255,0.56)" stroke="#475569" stroke-width="${mini ? 3.2 : 1.9}" stroke-linejoin="round"/>
    ${zoneSvg}
    <rect x="${(dtX - (mini ? 4.5 : 3.8)).toFixed(1)}" y="${(dtY - (mini ? 4.5 : 3.8)).toFixed(1)}" width="${mini ? 9 : 7.6}" height="${mini ? 9 : 7.6}" rx="1.5" fill="#1e293b"/>
    ${
      mini
        ? ""
        : `
      <text x="${dtX.toFixed(1)}" y="${dtLblY.toFixed(1)}" text-anchor="middle" font-family="system-ui, sans-serif" font-size="8.5" font-weight="800" fill="#1e293b" style="paint-order:stroke fill;stroke:rgba(255,255,255,0.92);stroke-width:2.5px">DOWNTOWN</text>
      <text x="${loopLabelX.toFixed(1)}" y="${(loopLabelY + 9).toFixed(1)}" text-anchor="middle" font-family="system-ui, sans-serif" font-size="8.5" font-weight="700" fill="#475569" style="paint-order:stroke fill;stroke:rgba(238,244,248,0.92);stroke-width:2.4px">610 LOOP</text>
      ${
        !fitsInnerFrame
          ? `<text x="${Math.min(vbW - 28, bwLabelX).toFixed(1)}" y="${Math.min(vbH - 5, bwLabelY + 8).toFixed(1)}" text-anchor="middle" font-family="system-ui, sans-serif" font-size="8" font-weight="700" fill="#64748b">BELTWAY 8</text>`
          : ""
      }
      ${freewayBadges}
      ${
        shortZone
          ? `<text x="${zLblX.toFixed(1)}" y="${zLblY.toFixed(1)}" text-anchor="middle" font-family="system-ui, sans-serif" font-size="8.5" font-weight="800" fill="#991b1b" style="paint-order:stroke fill;stroke:rgba(255,255,255,0.94);stroke-width:2.6px">${escHtml(shortZone)}</text>`
          : ""
      }
    `
    }
    <circle cx="${dotX.toFixed(1)}" cy="${dotY.toFixed(1)}" r="${mini ? 8.5 : 6.2}" fill="rgba(196,61,47,0.26)"/>
    <circle cx="${dotX.toFixed(1)}" cy="${dotY.toFixed(1)}" r="${mini ? 5.0 : 3.6}" fill="#c43d2f" stroke="#ffffff" stroke-width="${mini ? 1.8 : 1.3}"/>
  </svg>`;
}

/**
 * Renders the Option C parcel/footprint block thumbnail with the corner 610 Loop badge
 * that expands into the detailed Option B Houston city locator on hover (desktop) or tap (mobile).
 */
export function renderSpatialContextThumbnailHtml({
  lat,
  lng,
  polygonRings = [],
  neighborhood = "",
  historicDistrict = "",
  overlaysData = null,
  theme = "light",
  tone = "survey",
  width = 160,
  height = 102,
  ariaLabel = "Property block and Houston 610 Loop location preview",
} = {}) {
  const proj = buildBlockViewportProjection({
    lat,
    lng,
    polygonRings,
    width,
    height,
  });
  if (!proj) return "";

  const dtCtx = computeHoustonDowntownContext(proj.centerLat, proj.centerLng);
  const zoneHit = findContextZoneRingFromOverlays(overlaysData, {
    historicDistrict,
    neighborhood,
    lng: proj.centerLng,
    lat: proj.centerLat,
  });
  const cleanDistrict = cleanHistoricDistrictLabel(historicDistrict);
  const effectiveZoneLabel = sanitizeSpatialText(
    cleanDistrict || zoneHit?.label || String(neighborhood || "").trim() || dtCtx?.loopLabel || "Houston"
  ).trim();
  const effectiveZoneRings = zoneHit?.rings || zoneHit?.ring || null;

  const strokeByTone = {
    survey: { fill: "rgba(30,77,123,0.24)", stroke: "#1e4d7b", dot: "#c43d2f", dash: "" },
    critical: { fill: "rgba(225,29,72,0.24)", stroke: "#be123c", dot: "#e11d48", dash: "" },
    watch: { fill: "rgba(217,119,6,0.24)", stroke: "#b45309", dot: "#d97706", dash: "" },
    demolished: { fill: "rgba(100,116,139,0.22)", stroke: "#475569", dot: "#475569", dash: "4,2" },
  };
  const pal = strokeByTone[tone] || strokeByTone.survey;

  const miniLoopSvg = buildHoustonLoopLocatorSvg({
    lat: proj.centerLat,
    lng: proj.centerLng,
    width: 56,
    height: 38,
    mini: true,
    zoneLabel: effectiveZoneLabel,
    zoneRing: effectiveZoneRings,
  });

  const detailedLoopSvg = buildHoustonLoopLocatorSvg({
    lat: proj.centerLat,
    lng: proj.centerLng,
    width: 236,
    height: 142,
    mini: false,
    zoneLabel: effectiveZoneLabel,
    zoneRing: effectiveZoneRings,
  });

  const shapeSvg = proj.hasPolygon
    ? `<path d="${proj.svgPathData}" fill="${pal.fill}" stroke="${pal.stroke}" stroke-width="2.2" fill-rule="evenodd"${pal.dash ? ` stroke-dasharray="${pal.dash}"` : ""}/>`
    : `<circle cx="${proj.dotX}" cy="${proj.dotY}" r="12" fill="${pal.fill}" stroke="${pal.stroke}" stroke-width="1.6"${pal.dash ? ` stroke-dasharray="${pal.dash}"` : ""}/>`;

  const footerLine = dtCtx ? dtCtx.summaryText : "";

  return `<div class="atlas-spatial-thumb atlas-spatial-${theme === "dark" ? "dark" : "light"}" style="width:${width}px;height:${height}px" data-spatial-thumb="1">
    <div class="atlas-spatial-clip" title="${escHtml(ariaLabel)}">
      <svg class="atlas-spatial-fallback-grid" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" aria-hidden="true">
        <rect width="${width}" height="${height}" fill="#f1efe9"/>
        <g stroke="#e2ddd2" stroke-width="0.8">
          <line x1="0" y1="${(height * 0.33).toFixed(1)}" x2="${width}" y2="${(height * 0.33).toFixed(1)}"/>
          <line x1="0" y1="${(height * 0.67).toFixed(1)}" x2="${width}" y2="${(height * 0.67).toFixed(1)}"/>
          <line x1="${(width * 0.33).toFixed(1)}" y1="0" x2="${(width * 0.33).toFixed(1)}" y2="${height}"/>
          <line x1="${(width * 0.67).toFixed(1)}" y1="0" x2="${(width * 0.67).toFixed(1)}" y2="${height}"/>
        </g>
      </svg>
      <img
        class="atlas-spatial-map-img"
        src="${escHtml(proj.streetMapUrl)}"
        alt=""
        loading="lazy"
        decoding="async"
        referrerpolicy="no-referrer"
        onerror="this.style.display='none'"
      />
      <svg class="atlas-spatial-overlay-svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escHtml(ariaLabel)}">
        ${shapeSvg}
        <circle cx="${proj.dotX}" cy="${proj.dotY}" r="3.3" fill="${pal.dot}" stroke="#ffffff" stroke-width="1.2"/>
        <g transform="translate(6,${height - 9})">
          <rect x="-2" y="-8" width="${Math.round(proj.scaleBarPx + 34)}" height="11" rx="2.5" fill="rgba(255,255,255,0.88)"/>
          <line x1="1" y1="-2.5" x2="${(1 + proj.scaleBarPx).toFixed(1)}" y2="-2.5" stroke="#1e293b" stroke-width="1.6"/>
          <text x="${(proj.scaleBarPx + 4).toFixed(1)}" y="0.5" font-family="system-ui, sans-serif" font-size="8.2" font-weight="700" fill="#1e293b">${escHtml(proj.scaleLabel)}</text>
        </g>
      </svg>
    </div>
    <button
      type="button"
      class="atlas-spatial-pip-btn"
      data-spatial-pip-toggle="1"
      aria-expanded="false"
      aria-label="Hover or tap to view detailed Houston 610 Loop location map"
      title="Hover or tap to expand Houston 610 Loop &amp; neighborhood map"
    >${miniLoopSvg}</button>
    <div class="atlas-spatial-pip-popover" role="region" aria-label="Houston 610 Loop context map">
      <div class="atlas-spatial-pip-head">
        <span class="atlas-spatial-pip-title">📍 ${escHtml(effectiveZoneLabel)}</span>
        <button type="button" class="atlas-spatial-pip-close" data-spatial-pip-close="1" aria-label="Close Houston locator">✕</button>
      </div>
      <div class="atlas-spatial-pip-canvas">${detailedLoopSvg}</div>
      ${footerLine ? `<div class="atlas-spatial-pip-foot">${escHtml(footerLine)}</div>` : ""}
    </div>
  </div>`;
}

const SPATIAL_THUMB_CSS = `
.atlas-spatial-thumb {
  position: relative;
  flex-shrink: 0;
  border-radius: 8px;
  border: 1px solid #cbd5e1;
  background: #f1efe9;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.08);
  overflow: visible;
  user-select: none;
}
.atlas-spatial-thumb:hover,
.atlas-spatial-thumb.is-pinned {
  z-index: 40;
}
.atlas-spatial-thumb.atlas-spatial-dark {
  border-color: #334155;
  box-shadow: 0 2px 6px rgba(2, 6, 23, 0.35);
}
.atlas-spatial-clip {
  position: relative;
  width: 100%;
  height: 100%;
  border-radius: 7px;
  overflow: hidden;
}
.atlas-spatial-fallback-grid,
.atlas-spatial-map-img,
.atlas-spatial-overlay-svg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  display: block;
}
.atlas-spatial-map-img {
  object-fit: cover;
  filter: saturate(0.88) contrast(1.02);
}
.atlas-spatial-overlay-svg {
  pointer-events: none;
}
.atlas-spatial-pip-btn {
  position: absolute;
  right: 4px;
  bottom: 4px;
  width: 56px;
  height: 38px;
  padding: 0;
  margin: 0;
  border: 1.5px solid #1e293b;
  border-radius: 5px;
  overflow: hidden;
  background: #eef4f8;
  cursor: pointer;
  box-shadow: 0 2px 6px rgba(15, 23, 42, 0.38);
  z-index: 3;
}
.atlas-spatial-pip-btn svg {
  display: block;
  width: 100%;
  height: 100%;
}
.atlas-spatial-pip-btn:focus-visible {
  outline: 2px solid #38bdf8;
  outline-offset: 1px;
}
.atlas-spatial-pip-popover {
  display: none;
  position: absolute;
  left: calc(100% + 8px);
  top: -2px;
  width: 250px;
  max-width: calc(100vw - 36px);
  padding: 7px;
  border-radius: 9px;
  background: #0f172a;
  color: #f1f5f9;
  border: 1.5px solid #60a5fa;
  box-shadow: 0 14px 32px rgba(2, 6, 23, 0.65);
  z-index: 60;
}
.atlas-spatial-pip-popover::before {
  content: "";
  position: absolute;
  left: -10px;
  top: 0;
  bottom: 0;
  width: 10px;
}
.atlas-spatial-thumb.flip-up .atlas-spatial-pip-popover {
  top: auto;
  bottom: -2px;
}
.atlas-spatial-thumb.stack-below .atlas-spatial-pip-popover {
  left: 0;
  top: calc(100% + 6px);
  bottom: auto;
}
.atlas-spatial-thumb.stack-below .atlas-spatial-pip-popover::before {
  left: 0;
  right: 0;
  top: -8px;
  bottom: auto;
  width: auto;
  height: 8px;
}
.atlas-spatial-thumb.stack-below.flip-up .atlas-spatial-pip-popover {
  top: auto;
  bottom: calc(100% + 6px);
}
@media (max-width: 640px) {
  .atlas-spatial-pip-popover {
    left: 0;
    top: calc(100% + 6px);
    bottom: auto;
  }
  .atlas-spatial-thumb.flip-up .atlas-spatial-pip-popover {
    top: auto;
    bottom: calc(100% + 6px);
  }
}
@media (hover: hover) and (pointer: fine) {
  .atlas-spatial-pip-btn:hover + .atlas-spatial-pip-popover,
  .atlas-spatial-pip-popover:hover {
    display: block;
  }
}
.atlas-spatial-thumb.is-pinned .atlas-spatial-pip-popover {
  display: block;
}
.atlas-spatial-pip-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  margin-bottom: 5px;
  font-size: 12px;
  font-weight: 700;
  color: #f8fafc;
}
.atlas-spatial-pip-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.atlas-spatial-pip-close {
  background: transparent;
  border: none;
  color: #94a3b8;
  font-size: 13px;
  line-height: 1;
  padding: 2px 5px;
  cursor: pointer;
  border-radius: 4px;
}
.atlas-spatial-pip-close:hover {
  color: #ffffff;
  background: rgba(255, 255, 255, 0.1);
}
.atlas-spatial-pip-canvas {
  border-radius: 5px;
  overflow: hidden;
  border: 1px solid #334155;
}
.atlas-spatial-pip-canvas svg {
  display: block;
  width: 100%;
  height: auto;
}
.atlas-spatial-pip-foot {
  margin-top: 5px;
  font-size: 11.5px;
  color: #cbd5e1;
  line-height: 1.3;
}
@media print {
  .atlas-spatial-pip-popover {
    display: none !important;
  }
}
`;

const BOUND_DOCS_SYM = Symbol.for("ph.atlas.spatialThumbBoundDocs");

function adjustFlipDirection(thumbEl) {
  if (!thumbEl || typeof thumbEl.getBoundingClientRect !== "function") return;
  try {
    const rect = thumbEl.getBoundingClientRect();
    const scrollParent = thumbEl.closest(
      ".permit-watch-modal-body, .inspector-body, #inspector-body, .inspector-content, #detail, .detail"
    );
    const winW = typeof window !== "undefined" ? window.innerWidth : 1440;
    const winH = typeof window !== "undefined" ? window.innerHeight : 900;
    const parentRect = scrollParent ? scrollParent.getBoundingClientRect() : null;
    const topLimit = parentRect ? Math.max(0, parentRect.top) : 0;
    const bottomLimit = parentRect ? Math.min(winH, parentRect.bottom) : winH;
    const rightLimit = parentRect ? Math.min(winW, parentRect.right) : winW;
    const shouldStackBelow = winW <= 640 || rect.right + 262 > rightLimit;
    const spaceBelow = bottomLimit - (shouldStackBelow ? rect.bottom : rect.top);
    const spaceAbove = (shouldStackBelow ? rect.top : rect.bottom) - topLimit;
    const shouldFlip = spaceBelow < 220 && spaceAbove >= 220;
    thumbEl.classList.toggle("stack-below", shouldStackBelow);
    thumbEl.classList.toggle("flip-up", shouldFlip);
  } catch (_err) {
    // Safe fallback in synthetic DOM unit tests
  }
}

function unpinAllSpatialThumbnails(doc) {
  const openList = doc?.querySelectorAll?.("[data-spatial-thumb].is-pinned");
  openList?.forEach((el) => {
    el.classList.remove("is-pinned");
    el.querySelector?.("[data-spatial-pip-toggle]")?.setAttribute("aria-expanded", "false");
  });
  return Boolean(openList && openList.length > 0);
}

/**
 * Injects shared stylesheet once and installs delegated hover/tap/Escape handlers on `doc`.
 */
export function ensureSpatialContextThumbnailsInitialized(doc = typeof document !== "undefined" ? document : null) {
  if (!doc || typeof doc.getElementById !== "function" || typeof doc.createElement !== "function") {
    return;
  }
  if (!doc.getElementById("atlas-spatial-thumb-styles") && doc.head?.appendChild) {
    const styleEl = doc.createElement("style");
    styleEl.id = "atlas-spatial-thumb-styles";
    styleEl.textContent = SPATIAL_THUMB_CSS;
    doc.head.appendChild(styleEl);
  }

  const registry =
    globalThis[BOUND_DOCS_SYM] || (globalThis[BOUND_DOCS_SYM] = new WeakSet());
  if (registry.has(doc) || typeof doc.addEventListener !== "function") {
    return;
  }
  registry.add(doc);

  const onHoverOrFocus = (e) => {
    const btn = e.target?.closest?.("[data-spatial-pip-toggle]");
    if (btn) {
      adjustFlipDirection(btn.closest("[data-spatial-thumb]"));
    }
  };
  doc.addEventListener("mouseover", onHoverOrFocus);
  doc.addEventListener("focusin", onHoverOrFocus);

  doc.addEventListener("click", (e) => {
    const toggleBtn = e.target?.closest?.("[data-spatial-pip-toggle]");
    if (toggleBtn) {
      e.preventDefault();
      e.stopPropagation();
      const wrap = toggleBtn.closest("[data-spatial-thumb]");
      if (!wrap) return;
      const wasPinned = wrap.classList.contains("is-pinned");
      unpinAllSpatialThumbnails(doc);
      if (!wasPinned) {
        adjustFlipDirection(wrap);
        wrap.classList.add("is-pinned");
        toggleBtn.setAttribute("aria-expanded", "true");
      }
      return;
    }

    const closeBtn = e.target?.closest?.("[data-spatial-pip-close]");
    if (closeBtn) {
      e.preventDefault();
      e.stopPropagation();
      const wrap = closeBtn.closest("[data-spatial-thumb]");
      if (wrap) {
        wrap.classList.remove("is-pinned");
        wrap.querySelector?.("[data-spatial-pip-toggle]")?.setAttribute("aria-expanded", "false");
      }
      return;
    }

    if (!e.target?.closest?.(".atlas-spatial-pip-popover")) {
      unpinAllSpatialThumbnails(doc);
    }
  });

  const keyTarget =
    doc.defaultView && typeof doc.defaultView.addEventListener === "function"
      ? doc.defaultView
      : doc;
  keyTarget.addEventListener(
    "keydown",
    (e) => {
      if (e.key === "Escape" && unpinAllSpatialThumbnails(doc)) {
        e.preventDefault?.();
        e.stopPropagation();
        e.stopImmediatePropagation?.();
      }
    },
    true
  );
}
