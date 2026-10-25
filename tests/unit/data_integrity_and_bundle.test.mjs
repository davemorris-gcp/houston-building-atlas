import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  extractHttpUrlsFromText,
  validateExternalAtlasUrl,
} from "../../app/js/urlValidators.js";

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
});

test("overlays.json contains all 22 GeoJSON FeatureCollections + HOLC grade summary with valid Harris County coordinates", () => {
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

test("hahc_coa_tracker.json, active_permit_alerts.json, plat_agenda_monitor.json, and property_watchlist.json schemas", () => {
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
  assert.ok(Array.isArray(watchlist.watchedProperties) && watchlist.watchedProperties.length >= 3);
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
    path.join(DATA_DIR, "citydir_index.json"),
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
