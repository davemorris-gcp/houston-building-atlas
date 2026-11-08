import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { HoustonAtlasApp } from "../../app/js/app.js";
import {
  HPL_SURVEY_CEILINGS,
  buildArchivalDrawingsCardHtml,
  buildPost1926CityDirNoteHtml,
  normalizeArchivalLookupAddress,
  resolveArchivalRecords,
} from "../../app/js/archivalHelpers.js";
import { isClaimSuppressed } from "../../app/js/curatedEdits.js";
import { validateExternalAtlasUrl } from "../../app/js/urlValidators.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "../..");
const DATA_DIR = path.join(REPO_ROOT, "app", "public", "data");
const DOCS_DATA_DIR = path.join(REPO_ROOT, "docs", "data");

function parseSimpleCsv(rawCsv) {
  const lines = rawCsv.trim().split(/\r?\n/);
  const parseLine = (line) => {
    const out = [];
    let cur = "";
    let inQuotes = false;
    for (let i = 0; i < line.length; i += 1) {
      const ch = line[i];
      if (ch === '"') {
        if (inQuotes && line[i + 1] === '"') {
          cur += '"';
          i += 1;
        } else {
          inQuotes = !inQuotes;
        }
      } else if (ch === "," && !inQuotes) {
        out.push(cur);
        cur = "";
      } else {
        cur += ch;
      }
    }
    out.push(cur);
    return out;
  };
  const headers = parseLine(lines[0]);
  return lines.slice(1).map((line) => {
    const vals = parseLine(line);
    const obj = {};
    headers.forEach((h, idx) => {
      obj[h] = vals[idx] ?? "";
    });
    return obj;
  });
}

function createFakeAtlasApp(overrides = {}) {
  const app = Object.create(HoustonAtlasApp.prototype);
  app.archivalIndex = null;
  app.citydirIndex = null;
  app._globalRejections = null;
  app.filterStore = {
    getState: () => ({
      layers: { historicMap: false },
      historicMapId: "",
    }),
  };
  Object.assign(app, overrides);
  return app;
}

test("UT-M10-1: _resolveArchivalRecords resolves by_key, #fp_1/#aux_1 baseId, and by_addr directional fallbacks with coll.kind classification, deduplication, and 6-item cap", () => {
  const fakeApp = createFakeAtlasApp({
    archivalIndex: {
      version: 1,
      collections: {
        "hpl:p17006coll220": {
          title: "HPL Houston Architectural Survey Photographs (RG D 0038)",
          repo: "HMRC / Houston Public Library Digital Archives",
          kind: "hpl_cdm",
          url: "https://cdm17006.contentdm.oclc.org/digital/collection/p17006coll220",
        },
        "custom_hpl_coll": {
          title: "Custom HPL Collection Without Prefix",
          kind: "hpl_cdm",
          url: "https://cdm17006.contentdm.oclc.org/digital/collection/p17006coll41",
        },
        "taro:ricewrc/00034": {
          title: "William Ward Watkin Papers",
          repo: "Rice Woodson Research Center",
          kind: "taro",
          url: "https://txarchives.org/ricewrc/finding_aids/00034.xml",
        },
      },
      items: {
        a1: {
          c: "hpl:p17006coll220",
          t: "Historic Bungalow Exterior",
          u: "https://cdm17006.contentdm.oclc.org/digital/collection/p17006coll220/id/101",
          m: "address",
          b: "RGD0038-001",
          d: "1926",
          y: 1926,
          img: "https://cdm17006.contentdm.oclc.org/digital/api/singleitem/image/p17006coll220/101/default.jpg",
        },
        a2: {
          c: "custom_hpl_coll",
          t: "Moderne Elevation Study",
          u: "https://cdm17006.contentdm.oclc.org/digital/collection/p17006coll41/id/8",
          m: "address",
          b: "MSS0097-008",
          y: 1938,
          a: "Kenneth Franzheim",
        },
        a3: {
          c: "taro:ricewrc/00034",
          t: "Walling Street Residence Drawings",
          u: "https://txarchives.org/ricewrc/finding_aids/00034.xml",
          m: "address",
          b: "Box 4, Folder 2",
          d: "1927",
          y: 1927,
          a: "William Ward Watkin",
        },
        // Duplicate of a3 (same url + title + boxFolder) -> must deduplicate
        a4: {
          c: "taro:ricewrc/00034",
          t: "Walling Street Residence Drawings",
          u: "https://txarchives.org/ricewrc/finding_aids/00034.xml",
          m: "address",
          b: "Box 4, Folder 2",
          y: 1927,
        },
        a5: {
          c: "taro:ricewrc/00034",
          t: "Directional Street Folder",
          u: "https://txarchives.org/ricewrc/finding_aids/00034.xml",
          m: "address",
          b: "Box 5, Folder 1",
          y: 1928,
        },
        a6: {
          c: "taro:ricewrc/00034",
          t: "No-Directional Fallback Folder",
          u: "https://txarchives.org/ricewrc/finding_aids/00034.xml",
          m: "address",
          b: "Box 5, Folder 2",
          y: 1929,
        },
        a7: {
          c: "taro:ricewrc/00034",
          t: "Extra Folder Six",
          u: "https://txarchives.org/ricewrc/finding_aids/00034.xml",
          m: "landmark",
          b: "Box 6, Folder 1",
          y: 1930,
        },
        a8: {
          c: "taro:ricewrc/00034",
          t: "Overflow Folder Seven (Capped)",
          u: "https://txarchives.org/ricewrc/finding_aids/00034.xml",
          m: "landmark",
          b: "Box 6, Folder 2",
          y: 1931,
        },
      },
      by_key: {
        "bld_base": ["a1"],
        "0620820000016": ["a2", "a3", "a4"],
      },
      by_addr: {
        "1119 N WALLING": ["a5"],
        "1119 WALLING": ["a6", "a7", "a8"],
      },
    },
  });

  const resolved = fakeApp._resolveArchivalRecords({
    id: "bld_base#fp_1",
    hcad_num: "0620820000016",
    address: "1119 N WALLING ST",
    year_built: 1926,
  });

  assert.equal(resolved.length, 6, "Should deduplicate a4 and cap output at 6 records");
  assert.deepEqual(
    resolved.map((r) => r.id),
    ["a1", "a2", "a3", "a5", "a6", "a7"]
  );
  assert.equal(resolved[0].sourceType, "hpl_cdm");
  assert.equal(resolved[1].sourceType, "hpl_cdm", "coll.kind === 'hpl_cdm' must classify as hpl_cdm");
  assert.equal(resolved[1].repoLabel, "Houston Public Library Digital Archives");
  assert.equal(resolved[2].sourceType, "taro");
  assert.equal(resolved[2].repoLabel, "Rice Woodson Research Center");
});

test("UT-M10-2: _resolveArchivalRecords enforces lost_* isolation, replacement-building anachronism guard, and isClaimSuppressed on field 'archival_record'", () => {
  const prevLocalStorage = globalThis.localStorage;
  const lsMap = new Map();
  globalThis.localStorage = {
    getItem: (k) => (lsMap.has(k) ? lsMap.get(k) : null),
    setItem: (k, v) => lsMap.set(k, String(v)),
    removeItem: (k) => lsMap.delete(k),
  };

  try {
    const archivalIndex = {
      version: 1,
      collections: {
        "taro:utaaa/00005": {
          title: "Alfred C. Finn Architectural Records",
          repo: "UT Austin Alexander Architectural Archive",
          kind: "taro",
          url: "https://txarchives.org/utaaa/finding_aids/00005.xml",
        },
        "hpl:p17006coll220": {
          title: "HPL Houston Architectural Survey Photographs (RG D 0038)",
          repo: "HMRC / Houston Public Library Digital Archives",
          kind: "hpl_cdm",
          url: "https://cdm17006.contentdm.oclc.org/digital/collection/p17006coll220",
        },
      },
      items: {
        a1: {
          c: "taro:utaaa/00005",
          t: "Demolished 1912 Hotel Drawings",
          u: "https://txarchives.org/utaaa/finding_aids/00005.xml",
          m: "landmark",
          b: "Job 104",
          y: 1912,
        },
        a2: {
          c: "hpl:p17006coll220",
          t: "1930 Historic Cottage at 500 Main St",
          u: "https://cdm17006.contentdm.oclc.org/digital/collection/p17006coll220/id/55",
          m: "address",
          b: "RGD0038-055",
          y: 1930,
        },
        a3: {
          c: "taro:utaaa/00005",
          t: "2002 Modern Tower Rendering",
          u: "https://txarchives.org/utaaa/finding_aids/00005.xml",
          m: "address",
          b: "Job 999",
          y: 2002,
        },
      },
      by_key: {
        lost_historic_hotel: ["a1"],
        "0010010000001": ["a3"],
      },
      by_addr: {
        "500 MAIN": ["a2", "a3"],
      },
    };

    const fakeApp = createFakeAtlasApp({ archivalIndex });

    // 1. lost_* isolation: must NOT pull standing parcel's by_key[hcad_num] or by_addr[address]
    const lostRes = fakeApp._resolveArchivalRecords({
      id: "lost_historic_hotel",
      is_lost_houston: true,
      hcad_num: "0010010000001",
      address: "500 MAIN ST",
      year_built: 1912,
    });
    assert.deepEqual(
      lostRes.map((r) => r.id),
      ["a1"],
      "lost_* feature must never inherit extant parcel's by_key[hcad] or by_addr records"
    );

    // 2. Replacement-building anachronism guard: 2005 building at 500 MAIN ST rejects viaAddr 1930 record (2005 > 1930 + 20)
    const modernReplacementRes = fakeApp._resolveArchivalRecords({
      id: "bld_modern_500",
      hcad_num: "9999990000001",
      address: "500 MAIN ST",
      year_built: 2005,
    });
    assert.deepEqual(
      modernReplacementRes.map((r) => r.id),
      ["a3"],
      "Extant 2005 replacement building must filter out 1930 viaAddr record while keeping 2002 record"
    );

    // Contemporaneous extant 1945 building at 500 MAIN ST keeps 1930 viaAddr record (1945 <= 1930 + 20)
    const historicExtantRes = fakeApp._resolveArchivalRecords({
      id: "bld_hist_500",
      hcad_num: "9999990000002",
      address: "500 MAIN ST",
      year_built: 1945,
    });
    assert.deepEqual(
      historicExtantRes.map((r) => r.id),
      ["a2", "a3"]
    );

    // 3. Quick-flag suppression via globalRejections & local hidden claims on field: "archival_record"
    fakeApp._globalRejections = {
      rejectedClaims: [
        {
          hcadNum: "9999990000002",
          field: "archival_record",
          disputedValue: "1930 Historic Cottage at 500 Main St",
        },
      ],
    };
    assert.equal(
      isClaimSuppressed(
        {
          hcadNum: "9999990000002",
          field: "archival_record",
          value: "1930 Historic Cottage at 500 Main St",
        },
        fakeApp._globalRejections
      ),
      true
    );
    const afterGlobalSuppress = fakeApp._resolveArchivalRecords({
      id: "bld_hist_500",
      hcad_num: "9999990000002",
      address: "500 MAIN ST",
      year_built: 1945,
    });
    assert.deepEqual(
      afterGlobalSuppress.map((r) => r.id),
      ["a3"]
    );

    // Also test localStorage ph_atlas_hidden_claims suppression
    lsMap.set(
      "ph_atlas_hidden_claims",
      JSON.stringify([
        {
          hcadNum: "9999990000002",
          field: "archival_record",
          value: "2002 modern tower rendering",
        },
      ])
    );
    const afterLocalSuppress = fakeApp._resolveArchivalRecords({
      id: "bld_hist_500",
      hcad_num: "9999990000002",
      address: "500 MAIN ST",
      year_built: 1945,
    });
    assert.deepEqual(afterLocalSuppress, []);
  } finally {
    globalThis.localStorage = prevLocalStorage;
  }
});

test("UT-M10-3: Live archival_index.json and building_photos.json uphold sequential zero-orphan keys, single-line serialization, URL validation, and confidential HCAD exclusion", () => {
  const rawArchival = fs.readFileSync(path.join(DATA_DIR, "archival_index.json"), "utf8");
  assert.equal(rawArchival.includes("\n"), false, "archival_index.json must be single-line compact JSON");
  assert.equal(/[\u2013\u2014]/.test(rawArchival), false, "archival_index.json must contain zero U+2013/U+2014 dashes");

  const archival = JSON.parse(rawArchival);
  assert.equal(archival.version, 1);
  assert.ok(archival.taro_collections_scanned >= 100);
  assert.equal(archival.hpl_collections_scanned, 6);

  const itemKeys = Object.keys(archival.items || {});
  assert.ok(itemKeys.length >= 500, `Expected >= 500 matched archival items, got ${itemKeys.length}`);
  for (let i = 0; i < itemKeys.length; i += 1) {
    assert.equal(
      itemKeys[i],
      `a${i + 1}`,
      `Expected sequential archival item key a${i + 1} at index ${i}, got ${itemKeys[i]}`
    );
  }

  const referencedIds = new Set();
  for (const [propKey, arr] of Object.entries(archival.by_key || {})) {
    assert.ok(Array.isArray(arr) && arr.length > 0, `Empty by_key array for ${propKey}`);
    for (const aid of arr) {
      assert.ok(aid in archival.items, `Dangling archival item ID ${aid} in by_key[${propKey}]`);
      referencedIds.add(aid);
    }
  }
  for (const [addrKey, arr] of Object.entries(archival.by_addr || {})) {
    assert.ok(Array.isArray(arr) && arr.length > 0, `Empty by_addr array for ${addrKey}`);
    for (const aid of arr) {
      assert.ok(aid in archival.items, `Dangling archival item ID ${aid} in by_addr[${addrKey}]`);
      referencedIds.add(aid);
    }
  }
  assert.equal(
    referencedIds.size,
    itemKeys.length,
    "Every item in archival_index.json must be referenced by at least one by_key or by_addr entry (zero orphans)"
  );
  assert.deepEqual(
    Object.keys(archival.by_addr || {}).filter((k) => k.startsWith("0 ")),
    [],
    "archival_index.json by_addr must never index unnumbered '0 <STREET>' placeholder addresses"
  );
  assert.equal(
    "1339430010001" in (archival.by_key || {}),
    false,
    "Suppressed 2014 shard HCAD 1339430010001 must not carry historic Knapp Chevrolet records"
  );
  assert.equal(
    "0052320000001" in (archival.by_key || {}),
    true,
    "Active 1940 Knapp Chevrolet override 0052320000001 must carry its archival survey record"
  );

  for (const [ck, coll] of Object.entries(archival.collections || {})) {
    assert.ok(coll.kind === "taro" || coll.kind === "hpl_cdm", `Unexpected collection kind on ${ck}: ${coll.kind}`);
    assert.equal(
      /^[A-Z0-9]+$/.test(String(coll.repo || "").trim()),
      false,
      `Collection ${ck} must use a human-readable repository label rather than raw uppercase code: ${coll.repo}`
    );
    const collUrlCheck = validateExternalAtlasUrl(coll.url);
    assert.equal(collUrlCheck.valid, true, `Invalid collection URL on ${ck}: ${coll.url} (${collUrlCheck.reason})`);
  }

  for (const [aid, item] of Object.entries(archival.items)) {
    assert.ok(item.c in archival.collections, `Item ${aid} references missing collection ${item.c}`);
    const uCheck = validateExternalAtlasUrl(item.u);
    assert.equal(uCheck.valid, true, `Invalid item URL on ${aid}: ${item.u} (${uCheck.reason})`);
    if (item.img) {
      const imgCheck = validateExternalAtlasUrl(item.img);
      assert.equal(imgCheck.valid, true, `Invalid thumbnail URL on ${aid}: ${item.img} (${imgCheck.reason})`);
    }
  }

  const rawPhotos = fs.readFileSync(path.join(DATA_DIR, "building_photos.json"), "utf8");
  assert.equal(rawPhotos.includes("\n"), false, "building_photos.json must be single-line compact JSON");
  assert.equal(/[\u2013\u2014]/.test(rawPhotos), false, "building_photos.json must contain zero U+2013/U+2014 dashes");
  const photosDoc = JSON.parse(rawPhotos);
  const hplArchPhotos = (photosDoc.photos || []).filter((p) =>
    /cdm17006\.contentdm\.oclc\.org\/digital\/collection\/p17006coll(?:41|175|199|220|231|238)\//.test(
      String(p.source_url || "")
    )
  );
  assert.ok(
    hplArchPhotos.length >= 100,
    `Expected >= 100 enriched HPL architectural photos, got ${hplArchPhotos.length}`
  );
  for (const p of photosDoc.photos || []) {
    if (p.hcad_num) {
      assert.match(
        String(p.hcad_num),
        /^\d{13}$/,
        `building_photos.json hcad_num must be empty or 13 digits, got: ${p.hcad_num}`
      );
    }
  }
  for (const p of hplArchPhotos) {
    assert.equal(validateExternalAtlasUrl(p.image_url).valid, true, `Bad HPL image_url: ${p.image_url}`);
    assert.equal(validateExternalAtlasUrl(p.thumb_url).valid, true, `Bad HPL thumb_url: ${p.thumb_url}`);
    assert.equal(validateExternalAtlasUrl(p.source_url).valid, true, `Bad HPL source_url: ${p.source_url}`);
    if (String(p.caption || "").includes("(Architect:")) {
      assert.equal(
        String(p.caption).endsWith(")."),
        true,
        `Truncated HPL architect suffix in caption: ${p.caption}`
      );
    }
  }
});

test("UT-M10-4: post1926NoteHtml, citydir_index.json post_1926_clearance, and 1927-1963 USCO clearance CSV verify 37 public-domain years with 0 renewals", () => {
  const citydirRaw = fs.readFileSync(path.join(DATA_DIR, "citydir_index.json"), "utf8");
  assert.equal(/[\u2013\u2014]/.test(citydirRaw), false, "citydir_index.json must contain zero U+2013/U+2014 dashes");
  const citydir = JSON.parse(citydirRaw);

  const clearance = citydir.post_1926_clearance;
  assert.ok(clearance, "citydir_index.json must include post_1926_clearance metadata");
  assert.deepEqual(clearance.span, [1927, 1963]);
  assert.equal(clearance.audited_year_count, 37);
  assert.equal(clearance.usco_class_r_re_renewals_found, 0);
  assert.equal(clearance.all_1927_1963_public_domain, true);
  assert.equal(clearance.csv_path, "docs/data/houston_city_directories_1927_1963_usco_clearance.csv");

  const csvRaw = fs.readFileSync(
    path.join(DOCS_DATA_DIR, "houston_city_directories_1927_1963_usco_clearance.csv"),
    "utf8"
  );
  assert.equal(/[\u2013\u2014]/.test(csvRaw), false, "USCO clearance CSV must contain zero U+2013/U+2014 dashes");
  const csvRows = parseSimpleCsv(csvRaw);
  assert.equal(csvRows.length, 37, "Expected exactly 37 directory years (1927..1963)");
  for (let idx = 0; idx < 37; idx += 1) {
    const expectedYr = 1927 + idx;
    const row = csvRows[idx];
    assert.equal(Number(row.directory_year), expectedYr);
    assert.equal(Number(row.usco_class_r_re_renewals_found), 0);
    assert.ok(row.copyright_status.startsWith("Public Domain"), `Row ${expectedYr} must be Public Domain`);
  }
  const row1941 = csvRows.find((r) => Number(r.directory_year) === 1941);
  assert.ok(row1941.usco_class_a_registrations.includes("A 155265"), "1941 edition must map USCO registration A 155265");

  // Verify post1926NoteHtml rendering on HoustonAtlasApp.prototype
  const fakeApp = createFakeAtlasApp({ citydirIndex: citydir });
  const makeBaseCtx = (yr, cityDirInfo) => ({
    title: "Test Residence",
    yr,
    hcadNum: "0620820000016",
    sanbornInfo: {
      hits: [],
      digitizedHits: [],
      utPclHits: [],
      printOnlyHits: [],
      recommendedOverlay: { id: "sanborn_1924", label: "1924 Sanborn" },
    },
    cityDirInfo,
    legalTokens: { subdivisionDesc: "MONTROSE", lot: "1", block: "2" },
    owner: "DOE JANE",
    protectionEval: { tier: "unprotected" },
    catalogEntryForDeed: null,
    historicParentPlat: null,
    isReplattedFromHistoricParent: false,
    subVal: "MONTROSE",
    archChips: [],
    builderChips: [],
    haifSearchTerm: "1119 Walling St",
    haifGoogleSearchUrl: "https://www.google.com/search?q=site%3Ahoustonarchitecture.com+1119+Walling",
    streetViewUrl: "https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=29.75,-95.36",
  });

  const baseAddrInfo = {
    hasStreetAddress: true,
    houseNum: "1119",
    streetStem: "Walling",
    cleanQuery: "1119 Walling",
    exactCityDirUrl:
      "https://cdm17006.contentdm.oclc.org/digital/collection/citydir/search/searchterm/1119%20Walling/field/all/mode/exact/conn/and/order/date",
    hplAllArchivesUrl:
      "https://cdm17006.contentdm.oclc.org/digital/search/searchterm/1119%20Walling",
    historicAliases: [],
    numberingTip: "",
  };

  const post1926Info = fakeApp._buildCityDirectoryResearchData(
    { hcad_num: "0620820000016", address: "1119 WALLING ST" },
    "0620820000016",
    1941,
    baseAddrInfo
  );
  assert.equal(post1926Info.isPost1926Build, true);
  const post1926Html = fakeApp._buildHouseHistoryResearchKitHtml(makeBaseCtx(1941, post1926Info));
  assert.ok(post1926Html.includes("Post-1926 Occupant Lookup (1927-1963 Verified Public Domain):"));
  assert.ok(post1926Html.includes("0 copyright renewals"));
  assert.ok(post1926Html.includes("trace 1941-present residents"));
  assert.ok(post1926Html.includes("Julia Ideson Building, 550 McKinney St"));
  assert.ok(post1926Html.includes("Woodson Research Center (Fondren Library)"));
  assert.ok(post1926Html.includes('href="https://txarchives.org/home"'));
  assert.equal(/[\u2013\u2014]/.test(post1926Html), false);

  const pre1927Info = fakeApp._buildCityDirectoryResearchData(
    { hcad_num: "0620820000016", address: "1119 WALLING ST" },
    "0620820000016",
    1924,
    baseAddrInfo
  );
  assert.equal(pre1927Info.isPost1926Build, false);
  const pre1927Html = fakeApp._buildHouseHistoryResearchKitHtml(makeBaseCtx(1924, pre1927Info));
  assert.equal(pre1927Html.includes("Post-1926 Occupant Lookup"), false);
});

test("UT-M10-5: Pure archivalHelpers.js enforces HPL survey ceilings, indirect parcel sub-building guards, lost_* flag isolation, and dynamic USCO metadata", () => {
  assert.equal(HPL_SURVEY_CEILINGS["hpl:p17006coll220"], 1981);
  assert.equal(normalizeArchivalLookupAddress("0 AVENIDA DE LAS AMERICAS"), "");
  assert.equal(normalizeArchivalLookupAddress("2700 N ALBANY ST STE 100, HOUSTON TX 77006"), "2700 N ALBANY");

  const idx = {
    version: 1,
    collections: {
      "hpl:p17006coll220": {
        title: "HPL Houston Architectural Survey Photographs (RG D 0038)",
        repo: "HMRC / Houston Public Library Digital Archives",
        kind: "hpl_cdm",
        url: "https://cdm17006.contentdm.oclc.org/digital/collection/p17006coll220",
      },
      "taro:ricewrc/00034": {
        title: "William Ward Watkin Papers",
        repo: "Rice Woodson Research Center",
        kind: "taro",
        url: "https://txarchives.org/ricewrc/finding_aids/00034.xml",
      },
    },
    items: {
      a1: {
        c: "hpl:p17006coll220",
        t: "Undated Pre-1981 Survey Photo at 2700 Albany",
        u: "https://cdm17006.contentdm.oclc.org/digital/collection/p17006coll220/id/29",
        img: "https://cdm17006.contentdm.oclc.org/digital/api/singleitem/image/p17006coll220/29/default.jpg",
        m: "address",
      },
      a2: {
        c: "taro:ricewrc/00034",
        t: "1923 Main Campus Administration Drawings",
        u: "https://txarchives.org/ricewrc/finding_aids/00034.xml",
        m: "landmark",
        b: "Box 2, Folder 5",
        y: 1923,
      },
      a3: {
        c: "taro:ricewrc/00034",
        t: "Demolished 1912 Hotel Drawings",
        u: "https://txarchives.org/ricewrc/finding_aids/00034.xml",
        m: "landmark",
        b: "Box 1, Folder 1",
        y: 1912,
      },
    },
    by_key: {
      "1331740010001": ["a2"],
      lost_historic_hotel: ["a3"],
    },
    by_addr: {
      "2700 ALBANY": ["a1"],
    },
  };

  // 1. Undated HPL survey ceiling (1981 + 5 = 1986): 2000 replacement rejected, 1938 historic building kept
  assert.deepEqual(
    resolveArchivalRecords(idx, { id: "1240560000010", address: "2700 ALBANY ST", year_built: 2000 }),
    []
  );
  assert.equal(
    resolveArchivalRecords(idx, { id: "1240560000099", address: "2700 ALBANY ST", year_built: 1938 }).length,
    1
  );

  // 2. Indirect parcel lookup onto 1960 sub-building (#b1) rejects 1923 parcel-level drawings (1960 > 1923 + 20)
  assert.deepEqual(
    resolveArchivalRecords(idx, {
      id: "1331740010001#b1",
      hcad_num: "1331740010001",
      year_built: 1960,
    }),
    []
  );
  assert.equal(
    resolveArchivalRecords(idx, {
      id: "1331740010001",
      hcad_num: "1331740010001",
      year_built: 1923,
    }).length,
    1
  );

  // 3. lost_* suppressKey isolation & buildArchivalDrawingsCardHtml attributes
  const lostProps = {
    id: "lost_historic_hotel",
    is_lost_houston: true,
    hcad_num: "0010010000001",
    year_built: 1912,
  };
  const notSuppressedByParcel = resolveArchivalRecords(idx, lostProps, {
    globalRejections: {
      rejectedClaims: [
        {
          hcadNum: "0010010000001",
          field: "archival_record",
          disputedValue: "Demolished 1912 Hotel Drawings",
        },
      ],
    },
  });
  assert.equal(notSuppressedByParcel.length, 1);
  assert.equal(notSuppressedByParcel[0].suppressKey, "lost_historic_hotel");

  const cardHtml = buildArchivalDrawingsCardHtml(notSuppressedByParcel);
  assert.ok(cardHtml.includes('data-flag-hcad="lost_historic_hotel"'));
  assert.equal(cardHtml.includes("onerror="), false);

  const suppressedByLostId = resolveArchivalRecords(idx, lostProps, {
    globalRejections: {
      rejectedClaims: [
        {
          hcadNum: "lost_historic_hotel",
          field: "archival_record",
          disputedValue: "Demolished 1912 Hotel Drawings",
        },
      ],
    },
  });
  assert.deepEqual(suppressedByLostId, []);

  // 4. Dynamic USCO clearance metadata rendering
  const dynamicNote = buildPost1926CityDirNoteHtml(1955, {
    span: [1927, 1963],
    verified_date: "2027-03-15",
    usco_class_r_re_renewals_found: 1,
    all_1927_1963_public_domain: false,
  });
  assert.ok(dynamicNote.includes("1927-1963 USCO Audit"));
  assert.ok(dynamicNote.includes("March 2027 U.S. Copyright Office audit"));
  assert.ok(dynamicNote.includes("1 copyright renewal recorded"));
});
