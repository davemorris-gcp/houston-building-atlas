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
    const cleanUrl = String(url).split(/[?#]/)[0];
    const decoded = decodeURIComponent(cleanUrl);
    const parts = decoded.split('/');
    let last = parts[parts.length - 1] || '';
    // Strip thumbnail prefix like "960px-" or "lossy-page1-960px-"
    last = last.replace(/^(?:lossy-page\d+-)?\d+px-/, '');
    return last.toLowerCase().replace(/[^a-z0-9.]+/g, '_');
  } catch {
    return String(url).split(/[?#]/)[0].toLowerCase();
  }
}

const LS_COMMUNITY_PHOTOS_KEY = 'ph_atlas_community_photos';

export function getLocalCommunityPhotos() {
  if (typeof localStorage === 'undefined') return [];
  try {
    const raw = localStorage.getItem(LS_COMMUNITY_PHOTOS_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function indexPhotoRecord(index, raw, sourceTier = 'curated') {
  if (!raw || !raw.image_url) return;
  const pushToMap = (map, key, item) => {
    if (!key) return;
    const list = map.get(key) || [];
    if (!list.some((existing) => existing.image_url === item.image_url)) {
      list.push(item);
      map.set(key, list);
    }
  };

  const item = {
    ...raw,
    thumb_url: raw.thumb_url || raw.image_url,
    full_url: raw.full_url || raw.image_url,
    photo_year: Number(raw.photo_year) || null,
    era_label:
      raw.era_label ||
      (raw.photo_year ? `${raw.photo_year} Archival Photo` : 'Community Archival Photo'),
    source_tier: raw.source_tier || sourceTier,
  };
  index.allPhotos.push(item);

  if (raw.building_id) {
    pushToMap(index.byBuildingId, String(raw.building_id).trim().toLowerCase(), item);
  }
  if (raw.hcad_num) {
    const cleanHcad = String(raw.hcad_num).split('#')[0].replace(/\D/g, '');
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

/**
 * Registers a user- or admin-contributed photograph in the active session and local storage
 * so it appears immediately in the Archival Photographs timeline.
 */
export async function registerSessionPhoto(photoRecord) {
  if (!photoRecord || !photoRecord.image_url) return null;
  const index = await loadCuratedPhotosIndex();
  indexPhotoRecord(index, photoRecord, 'community');
  if (typeof localStorage !== 'undefined') {
    const existing = getLocalCommunityPhotos().filter(
      (p) => p.image_url !== photoRecord.image_url
    );
    existing.unshift(photoRecord);
    localStorage.setItem(LS_COMMUNITY_PHOTOS_KEY, JSON.stringify(existing.slice(0, 100)));
  }
  return photoRecord;
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
      const resp = await fetch('./public/data/building_photos.json?v=20261007e');
      if (resp.ok) {
        const data = await resp.json();
        const photos = Array.isArray(data?.photos) ? data.photos : [];
        for (const raw of photos) {
          indexPhotoRecord(index, raw, 'curated');
        }
      }
    } catch (err) {
      console.warn('Could not load curated building_photos.json:', err);
    }
    for (const localPhoto of getLocalCommunityPhotos()) {
      indexPhotoRecord(index, localPhoto, 'community');
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
  // 1. Check structured EXIF / MediaWiki timestamps first (YYYY-MM-DD) so street numbers
  //    in titles/descriptions (like "1314 Andrews" or "2029") aren't mistaken for photo years.
  const dateFields = [ext?.DateTimeOriginal?.value, ext?.DateTime?.value];
  for (const raw of dateFields) {
    if (!raw) continue;
    const text = stripHtml(raw);
    const isoMatch = text.match(/\b(18[4-9]\d|19\d{2}|20[01]\d|202[0-6])[-/:]\d{1,2}[-/:]\d{1,2}\b/);
    if (isoMatch) {
      return Number(isoMatch[1]);
    }
    const yearMatch = text.match(/\b(18[4-9]\d|19\d{2}|20[01]\d|202[0-6])\b/);
    if (yearMatch) {
      return Number(yearMatch[1]);
    }
  }

  // 2. Fallback to title / description year token (capped at 2026)
  for (const raw of [title, ext?.ImageDescription?.value]) {
    if (!raw) continue;
    const text = stripHtml(raw);
    const match = text.match(/\b(18[4-9]\d|19\d{2}|20[01]\d|202[0-6])\b/);
    if (match) {
      return Number(match[1]);
    }
  }
  return null;
}

const LS_HIDDEN_PHOTOS_KEY = 'ph_atlas_hidden_photos';

export function getHiddenPhotoKeys() {
  if (typeof localStorage === 'undefined') return new Set();
  try {
    const raw = localStorage.getItem(LS_HIDDEN_PHOTOS_KEY);
    const arr = raw ? JSON.parse(raw) : [];
    return new Set(Array.isArray(arr) ? arr : []);
  } catch {
    return new Set();
  }
}

export function isPhotoHidden(imageUrl) {
  if (!imageUrl) return false;
  const hidden = getHiddenPhotoKeys();
  const key = extractFilenameKey(imageUrl);
  return hidden.has(imageUrl) || (key && hidden.has(key));
}

export function hideBuildingPhoto(imageUrl) {
  if (!imageUrl || typeof localStorage === 'undefined') return;
  const hidden = getHiddenPhotoKeys();
  hidden.add(imageUrl);
  const key = extractFilenameKey(imageUrl);
  if (key) hidden.add(key);
  try {
    localStorage.setItem(LS_HIDDEN_PHOTOS_KEY, JSON.stringify(Array.from(hidden)));
  } catch (err) {
    console.warn('Could not persist hidden photo key:', err);
  }
  liveCommonsCache.clear();
}

function extractTitleStemKey(title) {
  return String(title || '')
    .toLowerCase()
    .replace(/^file:/i, '')
    .replace(/\.[a-z0-9]+$/i, '')
    .replace(/\blccn\d+\b/gi, '')
    .replace(/\bhabs[\s_-]+[a-z0-9,-]+/gi, '')
    .replace(/\(\d+\s*of\s*\d+\)/gi, '')
    .replace(/\(\d+\)$/g, '')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()
    .slice(0, 65);
}

/**
 * Evaluates a Wikimedia Commons page for architectural, building, streetscape,
 * or park/landscape relevance, rejecting off-topic nearby objects (aircraft cockpits,
 * vehicles, portraits/selfies, convention cosplayers, museum exhibit close-ups, food, etc.).
 *
 * Returns `{ usable: boolean, score: number }`.
 */
function evaluateCommonsCandidate(page, {
  landmarkName = '',
  address = '',
  useCategory = '',
  sourceType = 'geosearch', // 'search' | 'geosearch'
} = {}) {
  const title = String(page?.title || '');
  const lowerTitle = title.toLowerCase();
  const ii = page?.imageinfo?.[0];
  if (!ii?.url) return { usable: false, score: -100 };

  // Support standard web bitmaps AND archival .tif/.tiff masters (Library of Congress HABS/HAER/Highsmith)
  // whenever Wikimedia Commons provides a rendered .jpg thumburl.
  const isStandardBitmap = /\.(jpg|jpeg|png|webp)$/i.test(lowerTitle);
  const isArchivalTiff = /\.(tif|tiff)$/i.test(lowerTitle) && Boolean(ii.thumburl);
  if (!isStandardBitmap && !isArchivalTiff) {
    return { usable: false, score: -100 };
  }

  if (isPhotoHidden(ii.thumburl || ii.url) || isPhotoHidden(ii.url)) {
    return { usable: false, score: -100 };
  }

  const ext = ii.extmetadata || {};
  const rawDesc = stripHtml(ext?.ImageDescription?.value || '');
  const rawCats = stripHtml(ext?.Categories?.value || '');
  const rawObj = stripHtml(ext?.ObjectName?.value || '');
  const combinedText = `${title.replace(/_/g, ' ')} | ${rawObj} | ${rawDesc} | ${rawCats}`.toLowerCase();

  // 1. Hard graphic / non-photo exclusions
  const bannedGraphicTokens = [
    'locator',
    'map_of',
    'map of',
    'flag_of',
    'flag of',
    'seal_of',
    'seal of',
    'coat_of_arms',
    'coat of arms',
    'logo',
    'icon',
    'signature',
    'plaque_only',
    'red_pog',
    'floor_plan',
    'floor plan',
    'diagram',
    'wordmark',
  ];
  for (const token of bannedGraphicTokens) {
    if (lowerTitle.includes(token) || combinedText.includes(token)) {
      return { usable: false, score: -100 };
    }
  }

  // 2. Hard negative subject filters:
  //    a) Aircraft / plane-spotting / vehicle / transit rolling stock close-ups
  //    b) Portraits, headshots, selfies, cosplay, protests, sports action, concerts
  //    c) Interior museum artifacts, paintings, fossils, specimens, food, menus
  const hasExplicitBuildingFocus =
    /\b(terminal building|hangar exterior|control tower|facade|façade|exterior|architecture|historic american buildings survey|habs|designed by architect|courthouse|city hall|library building)\b/i.test(
      combinedText
    );

  const negativeVehicleAndAircraftPatterns = [
    /\bc\/n\s*[\d-]/i,
    /\bs\/n\s*[\d-]/i,
    /\bserial\s+\d{2,4}-\d{3,6}\b/i,
    /\bcockpit\b/i,
    /\bflight\s+deck\b/i,
    /\bmain\s+cabin\b/i,
    /\blanding\s+gear\b/i,
    /\bfuselage\b/i,
    /\bnose\s+art\b/i,
    /\braffle\s+plane\b/i,
    /\bwin-a-plane\b/i,
    /\b(cessna|piper\s+pa|lockheed|bombardier|airbus|boeing|hawker\s+siddeley|learjet|gulfstream|beechcraft|mcdonnell\s+douglas|embraer|sikorsky|bell\s+\d{3})\b/i,
    /\(aircraft\)/i,
    /\baircraft\s+(at|in|on|fueling)\b/i,
    /\btank\s+truck/i,
    /\bseen\s+(at|departing|arriving|taxiing)\b/i,
    /\bon\s+display\s+at\b/i,
    /\bphotographs\s+by\s+alan\s+wilson\b/i,
    /\bin\s+aviation\s+in\s+the\s+united\s+states\b/i,
    /\b(license\s+plate|rolling\s+stock|locomotive|metro\s+bus|bus\s+route|fire\s+engine|police\s+car|ambulance)\b/i,
  ];

  if (!hasExplicitBuildingFocus) {
    for (const pat of negativeVehicleAndAircraftPatterns) {
      if (pat.test(combinedText)) {
        return { usable: false, score: -100 };
      }
    }
  }

  const negativePeopleEventsAndArtifactsPatterns = [
    /\b(portrait\s+of|headshot|selfie|cosplay|comicpalooza|anime\s+matsuri)\b/i,
    /\b(protest|demonstration|rally|picketing|marchers|marathon\s+runner)\b/i,
    /\b(performing\s+at|speaking\s+at|press\s+conference|autograph\s+session)\b/i,
    /\b(oil\s+on\s+canvas|watercolor\s+on|acrylic\s+on|painting\s+by|sculpture\s+by)\b/i,
    /\b(holotype|taxidermy|dinosaur\s+skeleton|fossil\s+of|specimen\s+of)\b/i,
    /\b(insects\s+of|spiders\s+of|fungi\s+of|arthropods\s+of)\b/i,
    /\b(food\s+in\s+houston|dishes\s+of|restaurant\s+meals|menu\s+of)\b/i,
  ];
  for (const pat of negativePeopleEventsAndArtifactsPatterns) {
    if (pat.test(combinedText)) {
      return { usable: false, score: -100 };
    }
  }

  // 3. Determine if the target property is a Park / Plaza / Bayou / Cultural Landscape
  const contextStr = `${landmarkName} ${address} ${useCategory}`.toLowerCase();
  const isLandscapeContext =
    /\b(park|plaza|square|garden|bayou|greenway|arboretum|conservancy|cemetery|esplanade|promenade|trail)\b/i.test(
      contextStr
    );

  let score = 0;

  // 4. Positive Signal A: Direct Landmark Name or Street Address Match
  const cleanLandmark = String(landmarkName || '')
    .replace(/\([^)]*\)/g, ' ')
    .replace(/\b(the|houston|tx|texas|building|center|complex)\b/gi, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase();

  if (cleanLandmark && cleanLandmark.length >= 4 && combinedText.includes(cleanLandmark)) {
    score += 45;
  } else if (landmarkName) {
    // Check significant tokens from landmarkName
    const tokens = String(landmarkName)
      .toLowerCase()
      .replace(/[^a-z0-9\s]/g, ' ')
      .split(/\s+/)
      .filter((t) => t.length >= 4 && !['houston', 'texas', 'harris', 'county', 'building', 'center', 'historic'].includes(t));
    const matchedTokens = tokens.filter((t) => combinedText.includes(t));
    if (tokens.length > 0 && matchedTokens.length >= Math.min(2, tokens.length)) {
      score += 30;
    }
  }

  const cleanAddr = normalizeAddressKey(address).toLowerCase();
  if (cleanAddr && cleanAddr.length >= 6 && combinedText.includes(cleanAddr)) {
    score += 40;
  }

  // 5. Positive Signal B: Authoritative Architectural Archive (LOC HABS / Highsmith / NRHP)
  if (
    /\b(historic american buildings survey|habs|haer|carol m\. highsmith|library of congress|national register of historic places|texas historical commission|lccn\d+)\b/i.test(
      combinedText
    )
  ) {
    score += 35;
  }

  // 6. Positive Signal C: Architectural / Building / Streetscape Vocabulary & Categories
  if (
    /\b(architecture|architectural|building|buildings|facade|façade|exterior|elevation|rotunda|dome|tower|skyscraper|courthouse|city hall|library|church|cathedral|chapel|synagogue|school|university|college|campus|hall|hospital|hotel|theater|theatre|bank|warehouse|station|depot|terminal|house|home|mansion|bungalow|cottage|historic district|art deco|beaux-arts|victorian|craftsman|modernist|postcard|skyline|aerial view)\b/i.test(
      combinedText
    )
  ) {
    score += 25;
  }

  // Slight penalty for close-up detail/plaque shots so full building exteriors rank ahead of them
  if (/\b(bas-relief|relief|detail|plaque|marker|inscription|cornerstone)\b/i.test(lowerTitle)) {
    score -= 6;
  }

  // 7. Positive Signal D: Landscape / Park Vocabulary (boosted when inspecting a park/plaza/cemetery)
  if (
    /\b(park|garden|landscape|plaza|square|fountain|reflection pool|conservatory|bayou|bridge|monument|memorial|statue|pavilion|pergola|gazebo|esplanade|live oak|cemetery)\b/i.test(
      combinedText
    )
  ) {
    score += isLandscapeContext ? 30 : 12;
  }

  // Minimum relevance threshold:
  // - Blind coordinate geosearch MUST have positive architectural/landscape/entity signals (score >= 25)
  // - Name-based search must have score >= 20
  const minThreshold = sourceType === 'geosearch' ? 25 : 20;
  return {
    usable: score >= minThreshold,
    score,
  };
}

function formatCommonsPageToPhoto(page, relevanceScore = 0) {
  const ii = page.imageinfo[0];
  const ext = ii.extmetadata || {};
  const cleanTitle = String(page.title || '')
    .replace(/^File:/i, '')
    .replace(/\.[a-z0-9]+$/i, '')
    .replace(/_/g, ' ')
    .trim();

  const year = parseCommonsYear(cleanTitle, ext);
  const rawDesc = stripHtml(ext?.ImageDescription?.value || '')
    .replace(/^Title:\s*/i, '')
    .trim();
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
    relevance_score: relevanceScore,
    _stem: extractTitleStemKey(page.title),
  };
}

/**
 * Queries Wikimedia Commons using an Architecture-First strategy:
 *   1. Exact Landmark / Building Name search first (high precision, includes LOC .tif archives)
 *   2. Tight Coordinate GeoSearch second, filtered through `evaluateCommonsCandidate`
 *   3. Deduplicates series of near-identical detail shots by title stem
 */
export async function fetchCommonsPhotos({
  lat,
  lng,
  landmarkName = '',
  address = '',
  useCategory = '',
  radiusMeters = 60,
}) {
  const cacheKey = `${Number(lat).toFixed(4)},${Number(lng).toFixed(4)}|${normalizeKey(landmarkName)}|${normalizeAddressKey(address)}`;
  if (liveCommonsCache.has(cacheKey)) {
    return liveCommonsCache.get(cacheKey).filter((p) => !isPhotoHidden(p.image_url));
  }

  const candidates = [];
  const seenFiles = new Set();

  const addPages = (pagesObj, sourceType) => {
    if (!pagesObj) return;
    const pages = Object.values(pagesObj);
    for (const page of pages) {
      const { usable, score } = evaluateCommonsCandidate(page, {
        landmarkName,
        address,
        useCategory,
        sourceType,
      });
      if (!usable) continue;
      const fileKey = extractFilenameKey(page?.imageinfo?.[0]?.url || page.title);
      if (seenFiles.has(fileKey)) continue;
      seenFiles.add(fileKey);
      candidates.push(formatCommonsPageToPhoto(page, score));
    }
  };

  const cleanLandmark = String(landmarkName || '').trim();
  const primaryLandmarkName = cleanLandmark
    .replace(/\s*\([^)]*\)\s*/g, ' ')
    .split(/\s+(?:&|\/|—|--)\s+/)[0]
    .replace(/\s+/g, ' ')
    .trim();
  const isGenericTitle =
    !primaryLandmarkName ||
    primaryLandmarkName.length < 5 ||
    /^\d+\s+[A-Z0-9]/i.test(primaryLandmarkName) ||
    /^(commercial|residential|historic|single-family|multi-family|industrial|government|religious|educational|building|structure|standard tax parcel)/i.test(
      primaryLandmarkName
    );

  try {
    // 1. Entity / Landmark Name Search FIRST (highest architectural fidelity)
    if (!isGenericTitle) {
      const searchParams = new URLSearchParams({
        action: 'query',
        generator: 'search',
        gsrsearch: `"${primaryLandmarkName}" Houston`,
        gsrnamespace: '6',
        gsrlimit: '12',
        prop: 'imageinfo',
        iiprop: 'url|extmetadata',
        iiurlwidth: '960',
        format: 'json',
        origin: '*',
      });
      const searchResp = await fetch(`${COMMONS_API_ENDPOINT}?${searchParams.toString()}`);
      if (searchResp.ok) {
        const searchData = await searchResp.json();
        addPages(searchData?.query?.pages, 'search');
      }
    }

    // 2. Coordinate GeoSearch SECOND (only when needed, and strictly filtered for architecture/landscape)
    if (candidates.length < 5 && Number.isFinite(lat) && Number.isFinite(lng)) {
      const effectiveRadius = isGenericTitle ? Math.min(radiusMeters, 28) : Math.min(radiusMeters, 60);
      const geoParams = new URLSearchParams({
        action: 'query',
        generator: 'geosearch',
        ggscoord: `${Number(lat).toFixed(6)}|${Number(lng).toFixed(6)}`,
        ggsradius: String(effectiveRadius),
        ggsnamespace: '6',
        ggslimit: '12',
        prop: 'imageinfo',
        iiprop: 'url|extmetadata',
        iiurlwidth: '960',
        format: 'json',
        origin: '*',
      });
      const geoResp = await fetch(`${COMMONS_API_ENDPOINT}?${geoParams.toString()}`);
      if (geoResp.ok) {
        const geoData = await geoResp.json();
        addPages(geoData?.query?.pages, 'geosearch');
      }
    }
  } catch (err) {
    console.warn('Wikimedia Commons photo lookup error:', err);
  }

  // Sort by relevance score descending first to deduplicate near-identical series stems
  candidates.sort((a, b) => (b.relevance_score || 0) - (a.relevance_score || 0));
  const results = [];
  const seenStems = new Set();
  for (const item of candidates) {
    if (item._stem && seenStems.has(item._stem)) continue;
    if (item._stem) seenStems.add(item._stem);
    results.push(item);
    if (results.length >= 6) break;
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
  useCategory = '',
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
  const seenStems = new Set();
  const combined = [];

  for (const item of curated) {
    if (isPhotoHidden(item.image_url)) continue;
    const key = extractFilenameKey(item.image_url);
    if (key) seenFiles.add(key);
    const stem = extractTitleStemKey(item.source_url || item.image_url);
    if (stem) seenStems.add(stem);
    combined.push(item);
  }

  // Supplement with architecturally verified Wikimedia Commons photos if fewer than 4 curated photos exist
  if (combined.length < 4 && (Number.isFinite(lat) || landmarkName)) {
    const commons = await fetchCommonsPhotos({
      lat,
      lng,
      landmarkName,
      address,
      useCategory,
      radiusMeters: combined.length > 0 ? 35 : 45,
    });
    for (const item of commons) {
      if (isPhotoHidden(item.image_url)) continue;
      const key = extractFilenameKey(item.image_url);
      if (key && seenFiles.has(key)) continue;
      if (item._stem && seenStems.has(item._stem)) continue;
      if (key) seenFiles.add(key);
      if (item._stem) seenStems.add(item._stem);
      combined.push(item);
      if (combined.length >= 6) break;
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
