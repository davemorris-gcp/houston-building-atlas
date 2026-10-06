/**
 * HCAD Deep-Link Generator (search.hcad.org/SearchResults/<token>)
 *
 * HCAD's search portal (search.hcad.org) uses time-stamped, server-encrypted
 * tokens for property record URLs:
 *   base64("<ISO_Timestamp>*<Encrypted_Account_And_Year>*<Salt>")
 *
 * HCAD's official GIS Parcel Viewer (arcweb.hcad.org/parcel-viewer-v2.0) mints
 * these deep-link tokens on demand via CORS POST to:
 *   https://api.hcad.org/propertysearch/ExternalAccess/AccountDetails
 * authenticated with a time-based MD5 Basic token.
 */

const HCAD_EXTERNAL_ACCESS_URL =
  "https://api.hcad.org/propertysearch/ExternalAccess/AccountDetails";
const HCAD_CLIENT_ID = "aR847wAP6cWQpdzF";
const HCAD_CLIENT_SECRET = "FJce8LGkX3qbtTKrdnYC4EvD52uMSWNh";
const HCAD_ENDPOINT_PATH = "/AccountDetails";

/**
 * Pure JavaScript RFC 1321 MD5 hex digest for ASCII strings.
 * Avoids any external CDN dependency while matching CryptoJS.MD5().toString().
 */
export function md5Hex(str) {
  function rotateLeft(lValue, iShiftBits) {
    return (lValue << iShiftBits) | (lValue >>> (32 - iShiftBits));
  }

  function addUnsigned(lX, lY) {
    const lX8 = lX & 0x80000000;
    const lY8 = lY & 0x80000000;
    const lX4 = lX & 0x40000000;
    const lY4 = lY & 0x40000000;
    const lResult = (lX & 0x3fffffff) + (lY & 0x3fffffff);
    if (lX4 & lY4) return lResult ^ 0x80000000 ^ lX8 ^ lY8;
    if (lX4 | lY4) {
      if (lResult & 0x40000000) return lResult ^ 0xc0000000 ^ lX8 ^ lY8;
      return lResult ^ 0x40000000 ^ lX8 ^ lY8;
    }
    return lResult ^ lX8 ^ lY8;
  }

  function F(x, y, z) { return (x & y) | (~x & z); }
  function G(x, y, z) { return (x & z) | (y & ~z); }
  function H(x, y, z) { return x ^ y ^ z; }
  function I(x, y, z) { return y ^ (x | ~z); }

  function FF(a, b, c, d, x, s, ac) {
    a = addUnsigned(a, addUnsigned(addUnsigned(F(b, c, d), x), ac));
    return addUnsigned(rotateLeft(a, s), b);
  }
  function GG(a, b, c, d, x, s, ac) {
    a = addUnsigned(a, addUnsigned(addUnsigned(G(b, c, d), x), ac));
    return addUnsigned(rotateLeft(a, s), b);
  }
  function HH(a, b, c, d, x, s, ac) {
    a = addUnsigned(a, addUnsigned(addUnsigned(H(b, c, d), x), ac));
    return addUnsigned(rotateLeft(a, s), b);
  }
  function II(a, b, c, d, x, s, ac) {
    a = addUnsigned(a, addUnsigned(addUnsigned(I(b, c, d), x), ac));
    return addUnsigned(rotateLeft(a, s), b);
  }

  function convertToWordArray(string) {
    const lMessageLength = string.length;
    const lNumberOfWordsTemp1 = lMessageLength + 8;
    const lNumberOfWordsTemp2 = (lNumberOfWordsTemp1 - (lNumberOfWordsTemp1 % 64)) / 64;
    const lNumberOfWords = (lNumberOfWordsTemp2 + 1) * 16;
    const lWordArray = new Array(lNumberOfWords - 1).fill(0);
    let lBytePosition = 0;
    let lByteCount = 0;
    while (lByteCount < lMessageLength) {
      const lWordCount = (lByteCount - (lByteCount % 4)) / 4;
      lBytePosition = (lByteCount % 4) * 8;
      lWordArray[lWordCount] =
        lWordArray[lWordCount] | (string.charCodeAt(lByteCount) << lBytePosition);
      lByteCount++;
    }
    const lWordCount = (lByteCount - (lByteCount % 4)) / 4;
    lBytePosition = (lByteCount % 4) * 8;
    lWordArray[lWordCount] = lWordArray[lWordCount] | (0x80 << lBytePosition);
    lWordArray[lNumberOfWords - 2] = lMessageLength << 3;
    lWordArray[lNumberOfWords - 1] = lMessageLength >>> 29;
    return lWordArray;
  }

  function wordToHex(lValue) {
    let wordToHexValue = "";
    for (let lCount = 0; lCount <= 3; lCount++) {
      const lByte = (lValue >>> (lCount * 8)) & 255;
      const wordToHexValueTemp = "0" + lByte.toString(16);
      wordToHexValue += wordToHexValueTemp.substr(wordToHexValueTemp.length - 2, 2);
    }
    return wordToHexValue;
  }

  const x = convertToWordArray(str);
  let a = 0x67452301;
  let b = 0xefcdab89;
  let c = 0x98badcfe;
  let d = 0x10325476;

  const S11 = 7, S12 = 12, S13 = 17, S14 = 22;
  const S21 = 5, S22 = 9, S23 = 14, S24 = 20;
  const S31 = 4, S32 = 11, S33 = 16, S34 = 23;
  const S41 = 6, S42 = 10, S43 = 15, S44 = 21;

  for (let k = 0; k < x.length; k += 16) {
    const AA = a, BB = b, CC = c, DD = d;
    a = FF(a, b, c, d, x[k + 0], S11, 0xd76aa478);
    d = FF(d, a, b, c, x[k + 1], S12, 0xe8c7b756);
    c = FF(c, d, a, b, x[k + 2], S13, 0x242070db);
    b = FF(b, c, d, a, x[k + 3], S14, 0xc1bdceee);
    a = FF(a, b, c, d, x[k + 4], S11, 0xf57c0faf);
    d = FF(d, a, b, c, x[k + 5], S12, 0x4787c62a);
    c = FF(c, d, a, b, x[k + 6], S13, 0xa8304613);
    b = FF(b, c, d, a, x[k + 7], S14, 0xfd469501);
    a = FF(a, b, c, d, x[k + 8], S11, 0x698098d8);
    d = FF(d, a, b, c, x[k + 9], S12, 0x8b44f7af);
    c = FF(c, d, a, b, x[k + 10], S13, 0xffff5bb1);
    b = FF(b, c, d, a, x[k + 11], S14, 0x895cd7be);
    a = FF(a, b, c, d, x[k + 12], S11, 0x6b901122);
    d = FF(d, a, b, c, x[k + 13], S12, 0xfd987193);
    c = FF(c, d, a, b, x[k + 14], S13, 0xa679438e);
    b = FF(b, c, d, a, x[k + 15], S14, 0x49b40821);
    a = GG(a, b, c, d, x[k + 1], S21, 0xf61e2562);
    d = GG(d, a, b, c, x[k + 6], S22, 0xc040b340);
    c = GG(c, d, a, b, x[k + 11], S23, 0x265e5a51);
    b = GG(b, c, d, a, x[k + 0], S24, 0xe9b6c7aa);
    a = GG(a, b, c, d, x[k + 5], S21, 0xd62f105d);
    d = GG(d, a, b, c, x[k + 10], S22, 0x02441453);
    c = GG(c, d, a, b, x[k + 15], S23, 0xd8a1e681);
    b = GG(b, c, d, a, x[k + 4], S24, 0xe7d3fbc8);
    a = GG(a, b, c, d, x[k + 9], S21, 0x21e1cde6);
    d = GG(d, a, b, c, x[k + 14], S22, 0xc33707d6);
    c = GG(c, d, a, b, x[k + 3], S23, 0xf4d50d87);
    b = GG(b, c, d, a, x[k + 8], S24, 0x455a14ed);
    a = GG(a, b, c, d, x[k + 13], S21, 0xa9e3e905);
    d = GG(d, a, b, c, x[k + 2], S22, 0xfcefa3f8);
    c = GG(c, d, a, b, x[k + 7], S23, 0x676f02d9);
    b = GG(b, c, d, a, x[k + 12], S24, 0x8d2a4c8a);
    a = HH(a, b, c, d, x[k + 5], S31, 0xfffa3942);
    d = HH(d, a, b, c, x[k + 8], S32, 0x8771f681);
    c = HH(c, d, a, b, x[k + 11], S33, 0x6d9d6122);
    b = HH(b, c, d, a, x[k + 14], S34, 0xfde5380c);
    a = HH(a, b, c, d, x[k + 1], S31, 0xa4beea44);
    d = HH(d, a, b, c, x[k + 4], S32, 0x4bdecfa9);
    c = HH(c, d, a, b, x[k + 7], S33, 0xf6bb4b60);
    b = HH(b, c, d, a, x[k + 10], S34, 0xbebfbc70);
    a = HH(a, b, c, d, x[k + 13], S31, 0x289b7ec6);
    d = HH(d, a, b, c, x[k + 0], S32, 0xeaa127fa);
    c = HH(c, d, a, b, x[k + 3], S33, 0xd4ef3085);
    b = HH(b, c, d, a, x[k + 6], S34, 0x04881d05);
    a = HH(a, b, c, d, x[k + 9], S31, 0xd9d4d039);
    d = HH(d, a, b, c, x[k + 12], S32, 0xe6db99e5);
    c = HH(c, d, a, b, x[k + 15], S33, 0x1fa27cf8);
    b = HH(b, c, d, a, x[k + 2], S34, 0xc4ac5665);
    a = II(a, b, c, d, x[k + 0], S41, 0xf4292244);
    d = II(d, a, b, c, x[k + 7], S42, 0x432aff97);
    c = II(c, d, a, b, x[k + 14], S43, 0xab9423a7);
    b = II(b, c, d, a, x[k + 5], S44, 0xfc93a039);
    a = II(a, b, c, d, x[k + 12], S41, 0x655b59c3);
    d = II(d, a, b, c, x[k + 3], S42, 0x8f0ccc92);
    c = II(c, d, a, b, x[k + 10], S43, 0xffeff47d);
    b = II(b, c, d, a, x[k + 1], S44, 0x85845dd1);
    a = II(a, b, c, d, x[k + 8], S41, 0x6fa87e4f);
    d = II(d, a, b, c, x[k + 15], S42, 0xfe2ce6e0);
    c = II(c, d, a, b, x[k + 6], S43, 0xa3014314);
    b = II(b, c, d, a, x[k + 13], S44, 0x4e0811a1);
    a = II(a, b, c, d, x[k + 4], S41, 0xf7537e82);
    d = II(d, a, b, c, x[k + 11], S42, 0xbd3af235);
    c = II(c, d, a, b, x[k + 2], S43, 0x2ad7d2bb);
    b = II(b, c, d, a, x[k + 9], S44, 0xeb86d391);
    a = addUnsigned(a, AA);
    b = addUnsigned(b, BB);
    c = addUnsigned(c, CC);
    d = addUnsigned(d, DD);
  }
  return (wordToHex(a) + wordToHex(b) + wordToHex(c) + wordToHex(d)).toLowerCase();
}

const HCAD_ARCGIS_PUBLIC_QUERY_URL =
  "https://arcweb.hcad.org/server/rest/services/public/public_query/MapServer/0/query";
const HCAD_ARCGIS_RES_GRADE_URL =
  "https://arcweb.hcad.org/server/rest/services/public/Residential_Grade/MapServer/0/query";

const liveRecordCache = new Map();

/**
 * Builds the time-based Basic Authorization + AuthDate headers required by
 * https://api.hcad.org/propertysearch/ExternalAccess/AccountDetails
 */
export function buildHcadAuthHeaders(nowSec = Math.round(Date.now() / 1000)) {
  const digest = md5Hex(`${HCAD_CLIENT_SECRET}${nowSec}${HCAD_ENDPOINT_PATH}`);
  const b64 = btoa(`${HCAD_CLIENT_ID}:${digest}`);
  return {
    "Content-Type": "application/json",
    Accept: "*/*",
    Authorization: `Basic ${b64}`,
    AuthDate: String(nowSec),
  };
}

/**
 * Requests a fresh encrypted SearchResults deep-link URL from HCAD's
 * ExternalAccess API for the given 13-digit HCAD account number.
 */
export async function fetchHcadDeepLink(
  accountNumber,
  taxYear = String(new Date().getFullYear())
) {
  const cleanAcct = String(accountNumber || "").replace(/\D/g, "").trim();
  if (!cleanAcct) return null;

  const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
  const timeoutId = controller ? setTimeout(() => controller.abort(), 3500) : null;

  try {
    const headers = buildHcadAuthHeaders();
    const response = await fetch(HCAD_EXTERNAL_ACCESS_URL, {
      method: "POST",
      mode: "cors",
      cache: "no-cache",
      headers,
      signal: controller ? controller.signal : undefined,
      body: JSON.stringify({
        TaxYear: String(taxYear),
        Account: cleanAcct,
      }),
    });

    if (!response.ok) {
      throw new Error(`HCAD ExternalAccess returned HTTP ${response.status}`);
    }
    const url = (await response.text()).trim();
    if (url.startsWith("https://search.hcad.org/SearchResults/")) {
      return url;
    }
    return null;
  } finally {
    if (timeoutId) clearTimeout(timeoutId);
  }
}

/**
 * Fetches live HCAD appraisal, valuation, owner, state class, and legal description
 * data directly from HCAD's official ArcGIS REST MapServers (CORS-enabled, no token required).
 */
export async function fetchHcadLiveRecord(accountNumber) {
  const cleanAcct = String(accountNumber || "").replace(/\D/g, "").trim();
  if (!cleanAcct) return null;

  if (liveRecordCache.has(cleanAcct)) {
    return liveRecordCache.get(cleanAcct);
  }

  const whereParam = encodeURIComponent(`HCAD_NUM='${cleanAcct}'`);
  const publicQueryUrl = `${HCAD_ARCGIS_PUBLIC_QUERY_URL}?where=${whereParam}&outFields=*&returnGeometry=true&outSR=4326&f=geojson`;
  const resGradeUrl = `${HCAD_ARCGIS_RES_GRADE_URL}?where=${whereParam}&outFields=*&returnGeometry=false&f=json`;

  const [pubRes, gradeRes] = await Promise.allSettled([
    fetch(publicQueryUrl, { mode: "cors" }).then((r) => (r.ok ? r.json() : null)),
    fetch(resGradeUrl, { mode: "cors" }).then((r) => (r.ok ? r.json() : null)),
  ]);

  const pubFeature =
    pubRes.status === "fulfilled" &&
    pubRes.value &&
    Array.isArray(pubRes.value.features) &&
    pubRes.value.features[0]
      ? pubRes.value.features[0]
      : null;

  const pubAttrs = (pubFeature && (pubFeature.properties || pubFeature.attributes)) || {};

  const resAttrs =
    gradeRes.status === "fulfilled" &&
    gradeRes.value &&
    Array.isArray(gradeRes.value.features) &&
    gradeRes.value.features[0]
      ? gradeRes.value.features[0].attributes || {}
      : {};

  if (!pubAttrs.HCAD_NUM && !resAttrs.HCAD_NUM) {
    return null;
  }

  const geometry = (pubFeature && pubFeature.geometry) || null;
  let centroid = null;
  if (geometry && geometry.coordinates) {
    const ring =
      geometry.type === "Polygon"
        ? geometry.coordinates[0]
        : geometry.type === "MultiPolygon" && geometry.coordinates[0]
        ? geometry.coordinates[0][0]
        : null;
    if (Array.isArray(ring) && ring.length > 0) {
      let sumLng = 0;
      let sumLat = 0;
      let count = 0;
      for (const pt of ring) {
        if (Array.isArray(pt) && Number.isFinite(pt[0]) && Number.isFinite(pt[1])) {
          sumLng += pt[0];
          sumLat += pt[1];
          count += 1;
        }
      }
      if (count > 0) {
        centroid = [sumLng / count, sumLat / count];
      }
    }
  }

  const record = {
    hcadNum: cleanAcct,
    owner: (pubAttrs.owner || resAttrs.CurrOwner || "").trim() || null,
    address: (pubAttrs.address || resAttrs.LocAddr || "").trim() || null,
    city: (pubAttrs.city || resAttrs.city || "").trim() || null,
    zip: (pubAttrs.zip || resAttrs.zip || "").trim() || null,
    stateClass: (pubAttrs.state_class || resAttrs.StClsCode || "").trim() || null,
    landUseCode: (resAttrs.landuse || "").trim() || null,
    grade: (resAttrs.Grade || "").trim() || null,
    yearImpr: Number(resAttrs.year_impr) > 1800 ? Number(resAttrs.year_impr) : null,
    bldgSqft: Number(resAttrs.bldg_sqft) > 0 ? Number(resAttrs.bldg_sqft) : null,
    acreage: Number(resAttrs.acreage) > 0 ? Number(resAttrs.acreage) : null,
    lotAreaSqft:
      Number(pubAttrs["Shape.STArea()"]) > 0
        ? Math.round(Number(pubAttrs["Shape.STArea()"]))
        : null,
    appraisedVal: pubAttrs.appr_val != null ? Number(pubAttrs.appr_val) : null,
    marketVal: pubAttrs.mkt_val != null ? Number(pubAttrs.mkt_val) : null,
    imprVal: pubAttrs.impr_val != null ? Number(pubAttrs.impr_val) : null,
    landVal: pubAttrs.land_val != null ? Number(pubAttrs.land_val) : null,
    subdivision: (pubAttrs.subdivision || resAttrs.dscr || "").trim() || null,
    legalDescription: pubAttrs.legal_lines
      ? String(pubAttrs.legal_lines)
          .split("|")
          .map((s) => s.trim())
          .filter(Boolean)
          .join(" · ")
      : null,
    geometry,
    centroid,
    gisParcelUrl: `https://arcweb.hcad.org/parcel-viewer-v2.0/?hcad_num=${encodeURIComponent(cleanAcct)}`,
    searchPortalUrl: "https://search.hcad.org/",
  };

  liveRecordCache.set(cleanAcct, record);
  return record;
}

