/**
 * @fileoverview Historical & Contemporary Building Photograph Service.
 *
 * Combines two zero-cost, maintenance-light imagery tiers:
 *   1. Curated multi-era photographs from `public/data/building_photos.json`
 *      (synced with the `Building_Photos` tab in the Preservation Houston
 *      Google Sheet, including Library of Congress HABS, National Archives,
 *      and curated Wikimedia Commons historical & modern views).
 *   2. Live, zero-key queries to the Wikimedia Commons API (`origin=*` CORS):
 *      - Coordinate GeoSearch within 65m of the building footprint centroid
 *      - Bitmap title/description search when a named landmark is inspected
 *
 * Also builds direct Google Street View Time Machine deep-links (`2007–Present`).
 */

const COMMONS_API_ENDPOINT = 'https://commons.wikimedia.org/w/api.php';

let curatedIndexPromise = null;
let curatedIndex = null;
const liveCommonsCache = new Map();

function normalizeKey(str) {
  return String(str || '')
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, ' ')
    .trim();
}

function normalizeAddressKey(addr) {
  return String(addr || '')
    .toUpperCase()
    .replace(/\b(HOUSTON|TX|TEXAS|77\d{3})\b/g, '')
    .replace(/\bAVENUE\b/g, 'AVE')
    .replace(/\bSTREET\b/g, 'ST')
    .replace(/\bBOULEVARD\b/g, 'BLVD')
    .replace(/\bDRIVE\b/g, 'DR')
    .replace(/\bPARKWAY\b/g, 'PKWY')
    .replace(/\bFREEWAY\b/g, 'FWY')
    .replace(/[^A-Z0-9]+/g, ' ')
    .trim();
}

function stripHtml(html) {
  if (!html) return '';
  const text = String(html)
    .replace(/<style[\s\S]*?<\/style>/gi, '')
    .replace(/<script[\s\S]*?<\/script>/gi, '')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/&quot;/gi, '"')
    .replace(/&#039;/gi, "'")
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/\s+/g, ' ')
    .trim();
  return text;
}

function extractFilenameKey(url) {
  if (!url) return '';
  try {
    const decoded = decodeURIComponent(String(url));
    const parts = decoded.split('/');
    let last = parts[parts.length - 1] || '';
    // Strip thumbnail prefix like "960px-"
    last = last.replace(/^\d+px-/, '');
    return last.toLowerCase().replace(/[^a-z0-9.]+/g, '_');
  } catch {
    return String(url).toLowerCase();
  }
}

/**
 * Loads and indexes `public/data/building_photos.json` once.
 */
export async function loadCuratedPhotosIndex() {
  if (curatedIndex) return curatedIndex;
  if (curatedIndexPromise) return curatedIndexPromise;

  curatedIndexPromise = (async () => {
    const index = {
      byBuildingId: new Map(),
      byHcad: new Map(),
      byAddress: new Map(),
      byLandmark: new Map(),
      allPhotos: [],
    };
    try {
      const resp = await fetch('./public/data/building_photos.json');
      if (!resp.ok) return index;
      const data = await resp.json();
      const photos = Array.isArray(data?.photos) ? data.photos : [];
      index.allPhotos = photos;

      const pushToMap = (map, key, item) => {
        if (!key) return;
        const list = map.get(key) || [];
        list.push(item);
        map.set(key, list);
      };

      for (const raw of photos) {
        const item = {
          ...raw,
          photo_year: Number(raw.photo_year) || null,
          source_tier: 'curated',
        };
        if (raw.building_id) {
          pushToMap(index.byBuildingId, String(raw.building_id).trim().toLowerCase(), item);
        }
        if (raw.hcad_num) {
          const cleanHcad = String(raw.hcad_num).replace(/\D/g, '');
          if (cleanHcad) {
            pushToMap(index.byHcad, cleanHcad, item);
          }
        }
        if (raw.address) {
          pushToMap(index.byAddress, normalizeAddressKey(raw.address), item);
        }
        if (raw.landmark_name) {
          const normName = normalizeKey(raw.landmark_name);
          pushToMap(index.byLandmark, normName, item);
          const withoutThe = normName.replace(/^THE\s+/, '');
          if (withoutThe !== normName) {
            pushToMap(index.byLandmark, withoutThe, item);
          }
        }
      }
    } catch (err) {
      console.warn('Could not load curated building_photos.json:', err);
    }
    curatedIndex = index;
    return index;
  })();

  return curatedIndexPromise;
}

/**
 * Matches curated photos from `building_photos.json` for a building feature.
 */
export async function getCuratedBuildingPhotos({
  buildingId = '',
  hcadNum = '',
  landmarkName = '',
  address = '',
}) {
  const index = await loadCuratedPhotosIndex();
  const matched = [];
  const seenUrls = new Set();

  const addList = (list) => {
    if (!Array.isArray(list)) return;
    for (const item of list) {
      if (!item?.image_url || seenUrls.has(item.image_url)) continue;
      seenUrls.add(item.image_url);
      matched.push(item);
    }
  };

  if (buildingId) {
    const bid = String(buildingId).trim().toLowerCase();
    addList(index.byBuildingId.get(bid));
    if (bid.includes('#')) {
      addList(index.byBuildingId.get(bid.split('#')[0]));
    }
  }

  if (hcadNum) {
    const cleanHcad = String(hcadNum).replace(/\D/g, '');
    if (cleanHcad) {
      addList(index.byHcad.get(cleanHcad));
    }
  }

  if (address) {
    const normAddr = normalizeAddressKey(address);
    if (normAddr) {
      addList(index.byAddress.get(normAddr));
    }
  }

  if (landmarkName && matched.length === 0) {
    const normName = normalizeKey(landmarkName).replace(/^THE\s+/, '');
    if (normName.length >= 5) {
      addList(index.byLandmark.get(normName));
    }
  }

  return matched.sort((a, b) => (a.photo_year || 9999) - (b.photo_year || 9999));
}

function parseCommonsYear(title, ext) {
  const candidates = [
    ext?.DateTimeOriginal?.value,
    title,
    ext?.ImageDescription?.value,
    ext?.DateTime?.value,
  ];
  for (const raw of candidates) {
    if (!raw) continue;
    const text = stripHtml(raw);
    const match = text.match(/\b(18[4-9]\d|19\d{2}|20[0-2]\d)\b/);
    if (match) {
      return Number(match[1]);
    }
  }
  return null;
}

function isUsableCommonsImage(page) {
  const title = String(page?.title || '');
  const lower = title.toLowerCase();
  if (!/\.(jpg|jpeg|png|webp)$/i.test(lower)) return false;
  const bannedTokens = [
    'locator',
    'map_of',
    'flag_of',
    'seal_of',
    'logo',
    'icon',
    'signature',
    'plaque_only',
    'red_pog',
  ];
  for (const token of bannedTokens) {
    if (lower.includes(token)) return false;
  }
  const ii = page?.imageinfo?.[0];
  if (!ii?.url) return false;
  return true;
}

function formatCommonsPageToPhoto(page) {
  const ii = page.imageinfo[0];
  const ext = ii.extmetadata || {};
  const cleanTitle = String(page.title || '')
    .replace(/^File:/i, '')
    .replace(/\.[a-z0-9]+$/i, '')
    .replace(/_/g, ' ')
    .trim();

  const year = parseCommonsYear(cleanTitle, ext);
  const rawDesc = stripHtml(ext?.ImageDescription?.value || '');
  const caption =
    rawDesc && rawDesc.length > 8
      ? rawDesc.length > 210
        ? `${rawDesc.slice(0, 207)}...`
        : rawDesc
      : cleanTitle;

  const artist = stripHtml(ext?.Artist?.value || '');
  const license = stripHtml(ext?.LicenseShortName?.value || 'Wikimedia Commons');
  const credit = artist
    ? `Wikimedia Commons · ${artist.slice(0, 60)} (${license})`
    : `Wikimedia Commons (${license})`;

  let eraLabel = year ? `${year}` : 'Archival / Commons';
  if (year) {
    if (year < 1940) eraLabel = `${year} Historic`;
    else if (year < 1980) eraLabel = `${year} Mid-Century`;
    else if (year < 2005) eraLabel = `${year} Late 20th C.`;
    else eraLabel = `${year} Contemporary`;
  }

  return {
    photo_year: year,
    era_label: eraLabel,
    image_url: ii.thumburl || ii.url,
    thumb_url: ii.thumburl || ii.url,
    full_url: ii.url,
    caption,
    credit,
    source_url:
      ii.descriptionurl ||
      `https://commons.wikimedia.org/wiki/${encodeURIComponent(page.title)}`,
    is_primary: false,
    source_tier: 'commons_live',
  };
}

/**
 * Queries Wikimedia Commons by coordinate radius and (optionally) landmark name.
 */
export async function fetchCommonsPhotos({
  lat,
  lng,
  landmarkName = '',
  radiusMeters = 65,
}) {
  const cacheKey = `${Number(lat).toFixed(4)},${Number(lng).toFixed(4)}|${normalizeKey(landmarkName)}`;
  if (liveCommonsCache.has(cacheKey)) {
    return liveCommonsCache.get(cacheKey);
  }

  const results = [];
  const seenFiles = new Set();

  const addPages = (pagesObj) => {
    if (!pagesObj) return;
    const pages = Object.values(pagesObj);
    for (const page of pages) {
      if (!isUsableCommonsImage(page)) continue;
      const fileKey = extractFilenameKey(page?.imageinfo?.[0]?.url || page.title);
      if (seenFiles.has(fileKey)) continue;
      seenFiles.add(fileKey);
      results.push(formatCommonsPageToPhoto(page));
    }
  };

  try {
    if (Number.isFinite(lat) && Number.isFinite(lng)) {
      const geoParams = new URLSearchParams({
        action: 'query',
        generator: 'geosearch',
        ggscoord: `${Number(lat).toFixed(6)}|${Number(lng).toFixed(6)}`,
        ggsradius: String(radiusMeters),
        ggsnamespace: '6',
        ggslimit: '8',
        prop: 'imageinfo',
        iiprop: 'url|extmetadata',
        iiurlwidth: '960',
        format: 'json',
        origin: '*',
      });
      const geoResp = await fetch(`${COMMONS_API_ENDPOINT}?${geoParams.toString()}`);
      if (geoResp.ok) {
        const geoData = await geoResp.json();
        addPages(geoData?.query?.pages);
      }
    }

    const cleanLandmark = String(landmarkName || '').trim();
    const isGenericTitle =
      !cleanLandmark ||
      cleanLandmark.length < 5 ||
      /^\d+\s+[A-Z0-9]/i.test(cleanLandmark) ||
      /^(commercial|residential|historic|single-family|multi-family|industrial|government|religious|educational|building|structure)/i.test(
        cleanLandmark
      );

    if (!isGenericTitle && results.length < 4) {
      const searchParams = new URLSearchParams({
        action: 'query',
        generator: 'search',
        gsrsearch: `filetype:bitmap "${cleanLandmark}" Houston`,
        gsrnamespace: '6',
        gsrlimit: '6',
        prop: 'imageinfo',
        iiprop: 'url|extmetadata',
        iiurlwidth: '960',
        format: 'json',
        origin: '*',
      });
      const searchResp = await fetch(`${COMMONS_API_ENDPOINT}?${searchParams.toString()}`);
      if (searchResp.ok) {
        const searchData = await searchResp.json();
        addPages(searchData?.query?.pages);
      }
    }
  } catch (err) {
    console.warn('Wikimedia Commons photo lookup error:', err);
  }

  results.sort((a, b) => (a.photo_year || 9999) - (b.photo_year || 9999));
  liveCommonsCache.set(cacheKey, results);
  return results;
}

/**
 * Resolves the combined timeline of photographs for a building (Curated + Live Commons),
 * deduplicated by filename and sorted chronologically from earliest archival photo to present day.
 */
export async function resolveBuildingPhotos({
  buildingId = '',
  hcadNum = '',
  landmarkName = '',
  address = '',
  lat = null,
  lng = null,
}) {
  const curated = await getCuratedBuildingPhotos({
    buildingId,
    hcadNum,
    landmarkName,
    address,
  });

  const seenFiles = new Set();
  const combined = [];

  for (const item of curated) {
    const key = extractFilenameKey(item.image_url);
    if (key) seenFiles.add(key);
    combined.push(item);
  }

  // Always check live Wikimedia Commons if we have fewer than 4 curated photos,
  // so landmarks and downtown/historic buildings surface additional angles/eras.
  if (combined.length < 5 && (Number.isFinite(lat) || landmarkName)) {
    const commons = await fetchCommonsPhotos({
      lat,
      lng,
      landmarkName,
      radiusMeters: combined.length > 0 ? 50 : 65,
    });
    for (const item of commons) {
      const key = extractFilenameKey(item.image_url);
      if (key && seenFiles.has(key)) continue;
      if (key) seenFiles.add(key);
      combined.push(item);
      if (combined.length >= 8) break;
    }
  }

  combined.sort((a, b) => (a.photo_year || 9999) - (b.photo_year || 9999));
  return combined;
}

/**
 * Builds a Google Maps Street View panorama deep-link (`2007–Present` Time Machine).
 */
export function buildStreetViewUrl(lat, lng) {
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
    return 'https://www.google.com/maps/@29.7604,-95.3698,16z';
  }
  return `https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${Number(lat).toFixed(6)},${Number(lng).toFixed(6)}`;
}
