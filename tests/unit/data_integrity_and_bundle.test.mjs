import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  extractHttpUrlsFromText,
  validateExternalAtlasUrl,
} from "../../app/js/urlValidators.js";
import { parseHashToState } from "../../app/js/filterStore.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..", "..");
const APP_DIR = path.join(REPO_ROOT, "app");
const DATA_DIR = path.join(APP_DIR, "public", "data");
const ARCHETYPES_PATH = path.join(REPO_ROOT, "tests", "fixtures", "stratified_archetypes.json");

function readDataJson(name) {
  const fullPath = path.join(DATA_DIR, name);
  assert.ok(fs.existsSync(fullPath), `Expected data file to exist: ${fullPath}`);
  return JSON.parse(fs.readFileSync(fullPath, "utf8"));
}

function walkFiles(dir, acc = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      walkFiles(full, acc);
    } else if (entry.isFile()) {
      acc.push(full);
    }
  }
  return acc;
}

test("GitHub Pages bundle size limits (every file < 95 MB, total app/ < 980 MB)", () => {
  const files = walkFiles(APP_DIR);
  assert.ok(files.length >= 25, `Expected at least 25 files in app/, found ${files.length}`);

  const MAX_SINGLE_FILE_BYTES = 95 * 1024 * 1024;
  const MAX_TOTAL_BUNDLE_BYTES = 980 * 1024 * 1024;

  let totalBytes = 0;
  const oversized = [];
  for (const file of files) {
    const sz = fs.statSync(file).size;
    totalBytes += sz;
    if (sz >= MAX_SINGLE_FILE_BYTES) {
      oversized.push(`${path.relative(REPO_ROOT, file)} (${(sz / (1024 * 1024)).toFixed(2)} MB)`);
    }
  }

  assert.deepEqual(
    oversized,
    [],
    `Files exceeding GitHub Pages 95 MB single-file guardrail: ${oversized.join(", ")}`
  );
  assert.ok(
    totalBytes < MAX_TOTAL_BUNDLE_BYTES,
    `Total app/ bundle size ${(totalBytes / (1024 * 1024)).toFixed(2)} MB exceeds 980 MB GitHub Pages limit`
  );

  // Guardrail against local vs. GitHub Actions divergence:
  // 1) Zero git-ignored static assets inside app/public/ (which pass local Playwright but 404 on CI/Pages).
  const ignoredOut = execFileSync(
    "git",
    ["ls-files", "--others", "-i", "--exclude-standard", "app/public"],
    { cwd: REPO_ROOT, encoding: "utf8" }
  )
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
  assert.deepEqual(
    ignoredOut,
    [],
    `Git-ignored static files detected inside app/public/ (causes local-vs-CI HTTP 404 skew): ${ignoredOut.join(", ")}`
  );

  // 2) Exact 1:1 parity between HoustonAtlasApp.SHIPPED_EXPORT_CACHE_SLUGS and app/public/data/export_cache/*.json
  const appJsRaw = fs.readFileSync(path.join(APP_DIR, "js", "app.js"), "utf8");
  const slugBlockMatch = appJsRaw.match(/_shippedExportCacheSlugs\s*=\s*new Set\(\[([\s\S]*?)\]\)/);
  assert.ok(slugBlockMatch, "Expected HoustonAtlasApp.SHIPPED_EXPORT_CACHE_SLUGS Set definition in app/js/app.js");
  const declaredSlugs = Array.from(slugBlockMatch[1].matchAll(/"([a-z0-9_]+)"/g), (m) => m[1]).sort();
  assert.equal(declaredSlugs.length, 24, `Expected 24 shipped COH Historic/Heritage District export_cache slugs, got ${declaredSlugs.length}`);

  const exportCacheDir = path.join(DATA_DIR, "export_cache");
  assert.ok(fs.existsSync(exportCacheDir), "app/public/data/export_cache directory must exist");
  const onDiskSlugs = fs
    .readdirSync(exportCacheDir)
    .filter((f) => f.endsWith(".json"))
    .map((f) => f.slice(0, -5))
    .sort();
  assert.deepEqual(
    onDiskSlugs,
    declaredSlugs,
    "app/public/data/export_cache/*.json files must match HoustonAtlasApp.SHIPPED_EXPORT_CACHE_SLUGS 1:1"
  );
});

test("overlays.json contains all 23 GeoJSON FeatureCollections + HOLC grade summary with valid Harris County coordinates", () => {
  const overlays = readDataJson("overlays.json");
  const expectedFeatureCollections = [
    "landmarks",
    "historic_districts",
    "heritage_districts",
    "nrhp_districts",
    "thc_markers",
    "annexations",
    "good_brick_awards",
    "neighborhoods",
    "super_neighborhoods",
    "council_districts",
    "historic_wards",
    "platted_subdivisions",
    "land_use_protections",
    "historical_waterways",
    "historical_railroads",
    "lost_houston",
    "relocated_structures",
    "potential_historic_districts",
    "nrhp_properties",
    "cultural_districts",
    "red_book_1915",
    "holc_1937_zones",
    "hahc_coa_filings",
  ];

  for (const key of expectedFeatureCollections) {
    const fc = overlays[key];
    assert.ok(fc && fc.type === "FeatureCollection", `overlays.${key} must be a GeoJSON FeatureCollection`);
    assert.ok(
      Array.isArray(fc.features) && fc.features.length > 0,
      `overlays.${key} must contain at least 1 feature`
    );
  }
  assert.ok(
    overlays.holc_1937_grade_summary && typeof overlays.holc_1937_grade_summary === "object",
    "overlays.holc_1937_grade_summary must exist"
  );

  // Check COH Landmarks 3-tier classification integrity
  const landmarks = overlays.landmarks.features;
  assert.ok(landmarks.length >= 500, `Expected >= 500 COH landmarks, found ${landmarks.length}`);
  for (const feat of landmarks) {
    const tier = feat.properties?.protection_tier;
    assert.ok(
      tier === "plm" || tier === "lm_in_hd" || tier === "lm_waiver_vulnerable" || tier === "waiver_lm",
      `Unexpected landmark protection_tier '${tier}' on ${feat.properties?.name}`
    );
  }
});

test("sanborn_catalog.json schema, volume_boundaries array contract, and local UT PCL WebP plate mirrors", () => {
  const catalog = readDataJson("sanborn_catalog.json");
  assert.ok(Array.isArray(catalog.volumes) && catalog.volumes.length === 16, "Expected 16 Sanborn volumes");
  assert.ok(
    Array.isArray(catalog.volume_boundaries) && catalog.volume_boundaries.length >= 20,
    "volume_boundaries must be a non-empty Array (prevents TypeError on .find/.filter)"
  );

  let verifiedLocalPlates = 0;
  for (const vol of catalog.volumes) {
    assert.ok(vol.id && (vol.era || vol.date), "Each Sanborn volume requires id and era/date");
    for (const sheet of vol.sheets || []) {
      for (const field of ["thumb_url", "preview_url"]) {
        const relImg = String(sheet[field] || "");
        if (relImg && !relImg.startsWith("http")) {
          const cleanRel = relImg.replace(/^\/+/, "");
          const candidateA = path.join(APP_DIR, cleanRel);
          const candidateB = path.join(APP_DIR, "public", cleanRel);
          assert.ok(
            fs.existsSync(candidateA) || fs.existsSync(candidateB),
            `Missing local UT PCL Sanborn WebP plate on disk (${field}): ${relImg}`
          );
          verifiedLocalPlates += 1;
        }
      }
    }
  }
  assert.ok(
    verifiedLocalPlates >= 500,
    `Expected >= 500 locally mirrored UT PCL Sanborn WebP files (252 thumbs + 252 plates), verified ${verifiedLocalPlates}`
  );
});

test("deed_restrictions_catalog.json local PDFs exist on disk", () => {
  const deeds = readDataJson("deed_restrictions_catalog.json");
  const subEntries = Array.isArray(deeds.subdivisions)
    ? deeds.subdivisions
    : Object.values(deeds.subdivisions || {});
  assert.ok(subEntries.length >= 25, `Expected >= 25 deed restriction subdivisions, got ${subEntries.length}`);

  let verifiedPdfs = 0;
  for (const sub of subEntries) {
    for (const doc of sub.documents || []) {
      const localUrl = String(doc.local_url || "");
      if (localUrl) {
        const cleanRel = localUrl.replace(/^\/+/, "");
        const candidateA = path.join(APP_DIR, cleanRel);
        const candidateB = path.join(APP_DIR, "public", cleanRel);
        assert.ok(
          fs.existsSync(candidateA) || fs.existsSync(candidateB),
          `Missing mirrored deed restriction PDF on disk: ${localUrl}`
        );
        verifiedPdfs += 1;
      }
    }
  }
  assert.ok(verifiedPdfs >= 60, `Expected >= 60 verified local deed PDFs, found ${verifiedPdfs}`);
});

test("hahc_coa_tracker.json, active_permit_alerts.json, plat_agenda_monitor.json, property_watchlist.json, and monitor_activity_log.json schemas", () => {
  const hahc = readDataJson("hahc_coa_tracker.json");
  assert.ok(hahc.current_cycle && hahc.current_cycle.agenda_pdf_url, "hahc_coa_tracker current_cycle.agenda_pdf_url required");
  assert.ok(Array.isArray(hahc.filings) && hahc.filings.length >= 300, "Expected >= 300 HAHC filings");
  assert.ok(
    hahc.district_precedents && Object.keys(hahc.district_precedents).length >= 15,
    "Expected >= 15 district precedent summaries"
  );

  const permits = readDataJson("active_permit_alerts.json");
  assert.ok(Array.isArray(permits.alerts) && permits.alerts.length >= 1, "active_permit_alerts.alerts must be non-empty");

  const plat = readDataJson("plat_agenda_monitor.json");
  assert.ok(Array.isArray(plat.items), "plat_agenda_monitor.items must be an Array");

  const watchlist = readDataJson("property_watchlist.json");
  assert.ok(watchlist.primaryProperty?.hcadAccount, "property_watchlist.primaryProperty.hcadAccount required");
  assert.ok(Array.isArray(watchlist.watchedProperties) && watchlist.watchedProperties.length >= 1);
  for (const wp of watchlist.watchedProperties) {
    assert.notEqual(wp.trackerStatus, "Removed from Watchlist", `Removed tombstone leaked into active property_watchlist.json: ${wp.id}`);
    assert.notEqual(wp.trackerStatus, "Removed (Tier 3)", `Removed Tier 3 tombstone leaked into active property_watchlist.json: ${wp.id}`);
    if (String(wp.id || "").startsWith("plat-") && wp.trackerStatus !== "Archived (Tier 3)") {
      assert.equal(wp.tierConfirmed, true, `Unconfirmed plat seed leaked into property_watchlist.json: ${wp.id}`);
    }
  }
  assert.ok(
    watchlist.lastSweepHealth &&
      typeof watchlist.lastSweepHealth === "object" &&
      Number.isInteger(watchlist.lastSweepHealth.totalAgencies) &&
      Array.isArray(watchlist.lastSweepHealth.failedAgencies),
    "property_watchlist.lastSweepHealth telemetry object required"
  );

  const actLog = readDataJson("monitor_activity_log.json");
  assert.equal(actLog.version, 1, "monitor_activity_log.version must be 1");
  assert.equal(actLog.retentionDays, 90, "monitor_activity_log.retentionDays must be 90");
  assert.equal(actLog.timezone, "America/Chicago", "monitor_activity_log.timezone must be America/Chicago");
  assert.match(String(actLog.generatedAt || ""), /^\d{4}-\d{2}-\d{2}T/, "monitor_activity_log.generatedAt ISO string required");
  assert.ok(Array.isArray(actLog.dailyRuns) && actLog.dailyRuns.length >= 1, "monitor_activity_log.dailyRuns must be non-empty");
  assert.ok(Array.isArray(actLog.events) && actLog.events.length >= 10, "monitor_activity_log.events must be populated");

  const validStreams = new Set(["watchlist", "agenda", "staff", "permits"]);
  const seenEventIds = new Set();
  for (const ev of actLog.events) {
    assert.ok(ev.id && typeof ev.id === "string", "Every activity event requires string id");
    assert.equal(seenEventIds.has(ev.id), false, `Duplicate event id in monitor_activity_log.json: ${ev.id}`);
    seenEventIds.add(ev.id);
    assert.equal(validStreams.has(ev.stream), true, `Unexpected activity event stream '${ev.stream}' on ${ev.id}`);
    assert.match(String(ev.localDate || ""), /^\d{4}-\d{2}-\d{2}$/, `Invalid localDate on ${ev.id}`);
    assert.notEqual(
      ev.watchPropertyId,
      "plat-2026-1585",
      `Retracted unconfirmed seed plat-2026-1585 must not appear in monitor_activity_log.json (${ev.id})`
    );
    const evAddrUp = String(ev.address || "").toUpperCase();
    assert.equal(
      evAddrUp.includes("2603 MILAM"),
      false,
      `Misattributed 2603 MILAM seed address must not appear in monitor_activity_log.json (${ev.id})`
    );
    assert.equal(
      evAddrUp.includes("1226 W CLAY"),
      false,
      `Retracted 1226 W CLAY seed address must not appear in monitor_activity_log.json (${ev.id})`
    );
  }
});

test("Stratified 45-Archetype Matrix covers all 15 equivalence classes with valid Harris County WGS84 coordinates", () => {
  assert.ok(fs.existsSync(ARCHETYPES_PATH), "tests/fixtures/stratified_archetypes.json must exist");
  const matrix = JSON.parse(fs.readFileSync(ARCHETYPES_PATH, "utf8"));
  assert.equal(matrix.totalClasses, 15);
  assert.equal(matrix.archetypes.length, 45);

  const byClass = new Map();
  const seenIds = new Set();
  for (const arch of matrix.archetypes) {
    assert.ok(!seenIds.has(arch.archetypeId), `Duplicate archetypeId: ${arch.archetypeId}`);
    seenIds.add(arch.archetypeId);
    byClass.set(arch.classId, (byClass.get(arch.classId) || 0) + 1);

    assert.ok(
      arch.lng >= -96.2 && arch.lng <= -94.8,
      `Archetype ${arch.archetypeId} longitude ${arch.lng} out of Harris County bounds`
    );
    assert.ok(
      arch.lat >= 29.3 && arch.lat <= 30.3,
      `Archetype ${arch.archetypeId} latitude ${arch.lat} out of Harris County bounds`
    );
    assert.ok(
      arch.deepLinkHash && arch.deepLinkHash.startsWith("#"),
      `Archetype ${arch.archetypeId} missing deepLinkHash`
    );
  }

  assert.equal(byClass.size, 15, "All 15 equivalence classes must be represented");
  for (const [cls, count] of byClass.entries()) {
    assert.equal(count, 3, `Equivalence class ${cls} must have exactly 3 archetypes, got ${count}`);
  }
});

test("All static external URLs across HTML, JS, and authoritative data catalogs pass validateExternalAtlasUrl", () => {
  const filesToScan = [
    path.join(APP_DIR, "index.html"),
    path.join(APP_DIR, "plat-monitor.html"),
    ...fs
      .readdirSync(path.join(APP_DIR, "js"))
      .filter((f) => f.endsWith(".js") && f !== "urlValidators.js")
      .map((f) => path.join(APP_DIR, "js", f)),
    path.join(DATA_DIR, "overlays.json"),
    path.join(DATA_DIR, "sanborn_catalog.json"),
    path.join(DATA_DIR, "deed_restrictions_catalog.json"),
    path.join(DATA_DIR, "hahc_coa_tracker.json"),
    path.join(DATA_DIR, "active_permit_alerts.json"),
    path.join(DATA_DIR, "plat_agenda_monitor.json"),
    path.join(DATA_DIR, "property_watchlist.json"),
    path.join(DATA_DIR, "monitor_activity_log.json"),
    path.join(DATA_DIR, "citydir_index.json"),
    path.join(DATA_DIR, "neighborhood_scorecards.json"),
  ];

  const violations = [];
  let totalChecked = 0;

  for (const file of filesToScan) {
    const rel = path.relative(REPO_ROOT, file);
    const urls = extractHttpUrlsFromText(fs.readFileSync(file, "utf8"));
    for (const u of urls) {
      totalChecked += 1;
      const res = validateExternalAtlasUrl(u);
      if (!res.valid) {
        violations.push(`${rel} -> ${u} :: ${res.reason}`);
      }
    }
  }

  assert.ok(totalChecked >= 250, `Expected to scan >= 250 static external URLs, scanned ${totalChecked}`);
  assert.deepEqual(
    violations,
    [],
    `Found static external URL pattern violations:\n${violations.slice(0, 20).join("\n")}`
  );
});

test("Repository contains zero internal corporate proxy URLs, workstation paths, or non-PyPI lockfile mirrors", () => {
  const uvLockPath = path.join(REPO_ROOT, "pipeline", "uv.lock");
  assert.ok(fs.existsSync(uvLockPath), "pipeline/uv.lock must exist");
  const uvLockText = fs.readFileSync(uvLockPath, "utf8");
  const lockUrls = extractHttpUrlsFromText(uvLockText);
  assert.ok(lockUrls.length >= 25, `Expected >= 25 package URLs in pipeline/uv.lock, found ${lockUrls.length}`);
  const nonPublicLockUrls = lockUrls.filter(
    (u) =>
      !u.startsWith("https://pypi.org/") &&
      !u.startsWith("https://files.pythonhosted.org/")
  );
  assert.deepEqual(
    nonPublicLockUrls,
    [],
    `pipeline/uv.lock contains non-public PyPI mirror/proxy URLs (use 'uv lock --no-config --default-index https://pypi.org/simple'):\n${nonPublicLockUrls.slice(0, 10).join("\n")}`
  );

  const binaryExts = new Set([".pmtiles", ".webp", ".pdf", ".png", ".jpg", ".jpeg", ".ico", ".gif", ".woff", ".woff2"]);
  const selfRelPath = path.relative(REPO_ROOT, fileURLToPath(import.meta.url));
  const trackedFiles = execFileSync(
    "git",
    ["ls-files", "--cached", "--others", "--exclude-standard"],
    { cwd: REPO_ROOT, encoding: "utf8" }
  )
    .split("\n")
    .map((s) => s.trim())
    .filter((s) => s && s !== selfRelPath && !binaryExts.has(path.extname(s).toLowerCase()));

  const forbiddenRules = [
    { label: "internal .goog host/proxy URL", regex: /https?:\/\/[^\s"'<>]*\.goog\b/i },
    { label: "internal corporate domain", regex: /\b(?:corp\.google\.com|googleplex\.com|googlers\.com|internal\.google\.com|prod\.google\.com|uplink\.goog)\b/i },
    { label: "internal package proxy or redirector", regex: /\b(?:airlock-proxy|artifact-foundry|python-3p-limited|uberproxy|grounding-api-redirect|vertexaisearch\.cloud\.google\.com)\b/i },
    { label: "workstation or internal tool path", regex: /\/usr\/local\/google\b|\/google\/(?:bin|src|data)\b|\.gemini\/jetski\b|\b(?:gemini-agents|blaze-for-agents)\b/i },
    { label: "legacy sandbox asset path", regex: /\bph-ee-sandbox\b/i },
  ];

  const violations = [];
  for (const rel of trackedFiles) {
    const full = path.join(REPO_ROOT, rel);
    if (!fs.existsSync(full) || !fs.statSync(full).isFile()) continue;
    const content = fs.readFileSync(full, "utf8");
    for (const rule of forbiddenRules) {
      const m = content.match(rule.regex);
      if (m) {
        violations.push(`${rel} [${rule.label}]: ${m[0]}`);
      }
    }
  }

  assert.ok(trackedFiles.length >= 200, `Expected to scan >= 200 tracked text files, scanned ${trackedFiles.length}`);
  assert.deepEqual(
    violations,
    [],
    `Found internal/non-public corporate references in tracked repository files:\n${violations.slice(0, 25).join("\n")}`
  );
});

test("Historic subdivision de-replatting reconstructs pre-replat parent footprints and calibrates Harris County plat years", () => {
  const overlays = readDataJson("overlays.json");
  const searchIndex = readDataJson("search_index.json");
  const plats = overlays.platted_subdivisions?.features || [];
  assert.ok(plats.length >= 4500, `Expected >= 4,500 platted subdivisions, found ${plats.length}`);

  const byId = new Map(plats.map((f) => [f.properties.id, f]));

  const countInteriorHoles = (geom) => {
    if (!geom || !Array.isArray(geom.coordinates)) return 0;
    if (geom.type === "Polygon") {
      return Math.max(0, geom.coordinates.length - 1);
    }
    if (geom.type === "MultiPolygon") {
      return geom.coordinates.reduce(
        (acc, polyCoords) => acc + Math.max(0, polyCoords.length - 1),
        0
      );
    }
    return 0;
  };

  // 1. Fairview Addition (c. 1893, Harris County Deed Records Vol. 69, Pg. 368, HCAD 014-053)
  const fairview = byId.get("plat_1908_fairview");
  assert.ok(fairview, "plat_1908_fairview must exist in overlays.platted_subdivisions");
  const fProps = fairview.properties;
  assert.equal(fProps.is_historic_plat, true);
  assert.equal(fProps.estimated_plat_year, 1893);
  assert.equal(fProps.plat_year_label, "c. 1893");
  assert.equal(fProps.deed_citation, "Harris County Deed Records Vol. 69, Pg. 368");
  assert.equal(fProps.record_series, "Pre-1905 Harris County Deed Records (DR)");
  assert.ok(
    fProps.reconstructed_acres > fProps.surviving_acres + 2.0,
    `Expected Fairview reconstructed_acres (${fProps.reconstructed_acres}) > surviving_acres (${fProps.surviving_acres}) + 2.0`
  );
  assert.ok(
    fProps.healed_replat_count >= 25,
    `Expected Fairview healed_replat_count >= 25, got ${fProps.healed_replat_count}`
  );
  assert.equal(
    countInteriorHoles(fairview.geometry),
    0,
    "Expected reconstructed Fairview Addition polygon to have 0 interior replat doughnut holes"
  );
  assert.equal(
    fProps.bracey_1922_url,
    "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/21604"
  );

  // 2. Iconic Montrose, Heights, and inner-loop historic parent additions
  const expectedHistoricPlats = [
    { id: "plat_3791_montrose", year: 1911, zeroHoles: false, minHealed: 50 },
    { id: "plat_2751_hyde_park", year: 1895, zeroHoles: true, minHealed: 40 },
    { id: "plat_2756_hyde_park_main", year: 1923, zeroHoles: true, minHealed: 80 },
    { id: "plat_6366_westmoreland", year: 1902, zeroHoles: true, minHealed: 5 },
    { id: "plat_0272_avondale", year: 1907, zeroHoles: true, minHealed: 12 },
    { id: "plat_1316_courtlandt_place", year: 1906, zeroHoles: true, minHealed: 0 },
    { id: "plat_6592_woodland_heights", year: 1907, zeroHoles: true, minHealed: 5 },
    { id: "plat_3902_norhill", year: 1920, zeroHoles: true, minHealed: 1 },
    { id: "plat_2683_houston_heights", year: 1892, zeroHoles: false, minHealed: 500 },
    { id: "plat_1725_eastwood", year: 1911, zeroHoles: true, minHealed: 4 },
  ];
  for (const exp of expectedHistoricPlats) {
    const feat = byId.get(exp.id);
    assert.ok(feat, `${exp.id} must exist in overlays.platted_subdivisions`);
    const p = feat.properties;
    assert.equal(p.is_historic_plat, true, `${exp.id} must have is_historic_plat === true`);
    assert.equal(p.estimated_plat_year, exp.year, `${exp.id} expected year ${exp.year}, got ${p.estimated_plat_year}`);
    assert.ok(
      p.reconstructed_acres >= p.surviving_acres,
      `${exp.id} reconstructed_acres (${p.reconstructed_acres}) must be >= surviving_acres (${p.surviving_acres})`
    );
    assert.ok(
      p.healed_replat_count >= exp.minHealed,
      `${exp.id} expected healed_replat_count >= ${exp.minHealed}, got ${p.healed_replat_count}`
    );
    if (exp.zeroHoles) {
      assert.equal(
        countInteriorHoles(feat.geometry),
        0,
        `${exp.id} expected 0 interior replat doughnut holes`
      );
    }
  }

  // 3. Absorbed modern townhome cutouts and ROW/alley cadastral artifacts are suppressed
  for (const suppressedId of [
    "plat_1909_fairview_collective",
    "plat_1502_dedicated_per_plat",
    "plat_0006_aband_alley",
    "plat_0007_abandon_alley",
  ]) {
    assert.equal(
      byId.has(suppressedId),
      false,
      `Expected ${suppressedId} to be suppressed from overlays.platted_subdivisions`
    );
    assert.equal(
      searchIndex.some((row) => row.id === suppressedId),
      false,
      `Expected ${suppressedId} to be suppressed from search_index.json`
    );
  }

  // 4. Search index & URL deep-link resolution for Fairview Addition
  const fvSearchRow = searchIndex.find((row) => row.id === "plat_1908_fairview");
  assert.ok(fvSearchRow, "plat_1908_fairview must exist in search_index.json");
  assert.match(fvSearchRow.building_name, /Fairview.*1893/i);
  assert.match(fvSearchRow.address, /Deed Records Vol\. 69, Pg\. 368/i);

  const parsedUrl = parseHashToState("plats=1&bnd=plats:plat_1908_fairview");
  assert.equal(parsedUrl.patch.layers?.plattedSubdivisions, true);
  assert.equal(parsedUrl.selection?.boundaryLayer, "plattedSubdivisions");
  assert.equal(parsedUrl.selection?.boundaryId, "plat_1908_fairview");
});

test("UT-4, MOD-3 & MOD-4: compact curated_overrides.json schema, shard-primary campus # overrides, and uniform cache-buster parity", () => {
  const ovPath = path.join(DATA_DIR, "curated_overrides.json");
  const rawOv = fs.readFileSync(ovPath, "utf8");
  const ovBytes = Buffer.byteLength(rawOv, "utf8");
  assert.ok(
    ovBytes < 8_000_000,
    `curated_overrides.json (${ovBytes} bytes) must stay below 8,000,000-byte GitHub Pages budget`
  );
  assert.equal(
    rawOv.trimEnd().includes("\n"),
    false,
    "curated_overrides.json must remain single-line compact JSON"
  );

  const ovDoc = JSON.parse(rawOv);
  const overrides = ovDoc.overrides || ovDoc;
  const validContributing = new Set([
    "Contributing",
    "Non-Contributing",
    "Outside Historic District",
    "Tax-Exempt Campus Structure",
  ]);

  const splitCampusHcads = new Set([
    "1223580020001",
    "1468640010001",
    "0451440030163",
    "0500610000001",
    "0500620000001",
    "0500580000001",
    "0500500000006",
    "1378920010001",
    "0451440050245",
    "0410210010510",
  ]);
  const campusCountsByHcad = new Map();

  for (const [key, rec] of Object.entries(overrides)) {
    if (!rec || typeof rec !== "object") continue;
    if ("decade" in rec && rec.decade !== null && rec.decade !== undefined) {
      assert.equal(
        typeof rec.decade === "number" && Number.isInteger(rec.decade),
        true,
        `Override ${key} has non-integer decade: ${JSON.stringify(rec.decade)}`
      );
    }
    if ("contributing" in rec && rec.contributing !== null && rec.contributing !== undefined) {
      assert.equal(
        validContributing.has(rec.contributing),
        true,
        `Override ${key} has invalid contributing value: ${JSON.stringify(rec.contributing)}`
      );
    }

    const prefix = key.split("#")[0];
    if (splitCampusHcads.has(prefix)) {
      assert.equal(
        key.includes("#"),
        true,
        `Split campus parcel ${prefix} must use #<slug> sub-building keys, found bare key ${key}`
      );
      assert.equal(
        Boolean(rec.geometry),
        false,
        `Shard-primary campus override ${key} must not embed duplicate GeoJSON geometry`
      );
      assert.equal(rec.is_building_override, true, `${key} must set is_building_override === true`);
      assert.equal(rec.keep_shard_footprints, true, `${key} must set keep_shard_footprints === true`);
      assert.equal(rec.replace_parcel_shards, false, `${key} must set replace_parcel_shards === false`);
      assert.equal(
        Number.isFinite(Number(rec.lon)) && Number.isFinite(Number(rec.lat)),
        true,
        `Shard-primary campus override ${key} must carry finite lon/lat coordinates for #id= deep-link flight`
      );
      campusCountsByHcad.set(prefix, (campusCountsByHcad.get(prefix) || 0) + 1);
    }
  }

  for (const hcad of splitCampusHcads) {
    assert.ok(
      (campusCountsByHcad.get(hcad) || 0) >= 1,
      `Expected at least 1 #<slug> override on campus parcel ${hcad}`
    );
  }

  // MOD-4 & UT-4: Uniform cache-buster parity across pmtiles_manifest.json, schema.py, HTML entrypoints, ES module imports, and static public/data fetches
  const manifest = readDataJson("pmtiles_manifest.json");
  const expectedVer = String(manifest.cache_bust || "").trim();
  assert.equal(expectedVer, "20261030a", `Expected pmtiles_manifest.json cache_bust === '20261030a'`);

  const schemaPyRaw = fs.readFileSync(path.join(REPO_ROOT, "pipeline", "atlas_pipeline", "schema.py"), "utf8");
  const schemaVerMatch = schemaPyRaw.match(/^\s*ACTIVE_CACHE_BUST\s*=\s*["']([^"']+)["']/m);
  assert.ok(schemaVerMatch, "pipeline/atlas_pipeline/schema.py must declare ACTIVE_CACHE_BUST");
  assert.equal(
    schemaVerMatch[1],
    expectedVer,
    `pipeline/atlas_pipeline/schema.py ACTIVE_CACHE_BUST ('${schemaVerMatch[1]}') must match pmtiles_manifest.json ('${expectedVer}')`
  );

  const sourceFiles = [
    path.join(APP_DIR, "index.html"),
    path.join(APP_DIR, "plat-monitor.html"),
    ...fs
      .readdirSync(path.join(APP_DIR, "js"))
      .filter((f) => f.endsWith(".js"))
      .map((f) => path.join(APP_DIR, "js", f)),
  ];

  const verMatches = [];
  for (const file of sourceFiles) {
    const rel = path.relative(REPO_ROOT, file);
    const content = fs.readFileSync(file, "utf8");

    // 1. Every ?v=<token> literal must match manifest.cache_bust
    for (const m of content.matchAll(/\?v=([A-Za-z0-9._-]+)/g)) {
      verMatches.push(m[1]);
      assert.equal(
        m[1],
        expectedVer,
        `Stale cache-buster '?v=${m[1]}' in ${rel} (expected '?v=${expectedVer}')`
      );
    }

    // 2. Every relative ES module import must carry ?v=<expectedVer>
    for (const m of content.matchAll(/\bfrom\s+["'](\.[^"']+)["']/g)) {
      assert.equal(
        m[1].endsWith(`?v=${expectedVer}`),
        true,
        `Relative ES module import '${m[1]}' in ${rel} is missing '?v=${expectedVer}'`
      );
    }

    // 3. Every fetch(...) of public/data/ JSON/GeoJSON (except dynamic + cacheQ expressions) must carry ?v=<expectedVer>
    for (const m of content.matchAll(/fetch\(\s*[`"'](\.?\/?public\/data\/[^`"']+\.(?:json|geojson)[^`"']*)[`"']/g)) {
      const urlStr = m[1];
      const afterIdx = (m.index || 0) + m[0].length;
      const trailing = content.slice(afterIdx, afterIdx + 24);
      if (/^\s*\+\s*cacheQ\b/.test(trailing)) continue;
      assert.equal(
        urlStr.includes(`?v=${expectedVer}`),
        true,
        `Data fetch '${urlStr}' in ${rel} is missing cache-buster '?v=${expectedVer}'`
      );
    }
  }
  assert.ok(verMatches.length >= 40, `Expected >= 40 verified ?v=${expectedVer} tokens, found ${verMatches.length}`);

  // Typography hygiene: zero U+2014 em-dashes in curated_overrides.json, search_index.json, and all address_shards/*.json
  assert.equal(
    rawOv.includes("\u2014"),
    false,
    "curated_overrides.json must contain zero U+2014 em-dashes"
  );
  const rawSearchIndex = fs.readFileSync(path.join(DATA_DIR, "search_index.json"), "utf8");
  assert.equal(
    rawSearchIndex.includes("\u2014"),
    false,
    "search_index.json must contain zero U+2014 em-dashes"
  );
  const addrShardsDir = path.join(DATA_DIR, "address_shards");
  for (const fn of fs.readdirSync(addrShardsDir)) {
    if (!fn.endsWith(".json")) continue;
    const shardRaw = fs.readFileSync(path.join(addrShardsDir, fn), "utf8");
    assert.equal(
      shardRaw.includes("\u2014"),
      false,
      `address_shards/${fn} must contain zero U+2014 em-dashes`
    );
  }

  for (const relCheck of [
    "app/public/data/monitor_activity_log.json",
    "app/public/data/property_watchlist.json",
    "app/public/data/neighborhood_scorecards.json",
    "app/plat-monitor.html",
    "app/js/activityLogHelpers.js",
    "app/js/preservationOrganizer.js",
    "app/js/watchlistSyncHelpers.js",
    "pipeline/atlas_pipeline/build_monitor_activity_log.py",
    "pipeline/atlas_pipeline/build_neighborhood_scorecards.py",
    "pipeline/atlas_pipeline/fetch_plat_and_watchlist.py",
    ".github/workflows/update_live_permits.yml",
    "docs/PRESERVATION_MONITORING_AUTOMATION_ARCHITECTURE.md",
  ]) {
    const rawTxt = fs.readFileSync(path.join(REPO_ROOT, relCheck), "utf8");
    assert.equal(rawTxt.includes("\u2014"), false, `${relCheck} must contain zero U+2014 em-dashes`);
  }

  const scPath = path.join(DATA_DIR, "neighborhood_scorecards.json");
  const rawSc = fs.readFileSync(scPath, "utf8");
  assert.ok(
    Buffer.byteLength(rawSc, "utf8") < 2_500_000,
    `neighborhood_scorecards.json (${Buffer.byteLength(rawSc, "utf8")} bytes) must stay below 2,500,000-byte budget`
  );
  assert.equal(
    rawSc.trimEnd().includes("\n"),
    false,
    "neighborhood_scorecards.json must remain single-line compact JSON"
  );

  // Zero positional checklist[N] indexing across watchlist/activity pipeline & UI code
  for (const relNoPos of [
    "app/plat-monitor.html",
    "app/js/activityLogHelpers.js",
    "app/js/watchlistSyncHelpers.js",
    "pipeline/atlas_pipeline/fetch_plat_and_watchlist.py",
    "pipeline/atlas_pipeline/build_monitor_activity_log.py",
  ]) {
    const rawTxt = fs.readFileSync(path.join(REPO_ROOT, relNoPos), "utf8");
    assert.equal(
      /\bchecklist\[\s*\d+\s*\]/.test(rawTxt),
      false,
      `${relNoPos} must address watchlist checkpoints by row.id ('chk-01'..'chk-15'), never positional checklist[N]`
    );
  }

  // Ensure every unit test suite in tests/unit/*.test.mjs is registered in tests/unit/run_all.mjs
  const runAllRaw = fs.readFileSync(path.join(REPO_ROOT, "tests", "unit", "run_all.mjs"), "utf8");
  const testFiles = fs
    .readdirSync(path.join(REPO_ROOT, "tests", "unit"))
    .filter((f) => f.endsWith(".test.mjs"))
    .sort();
  assert.ok(testFiles.length >= 6, `Expected >= 6 unit test files, found ${testFiles.length}`);
  for (const tf of testFiles) {
    assert.equal(
      runAllRaw.includes(`./${tf}`),
      true,
      `tests/unit/${tf} is missing from tests/unit/run_all.mjs imports`
    );
  }
});
