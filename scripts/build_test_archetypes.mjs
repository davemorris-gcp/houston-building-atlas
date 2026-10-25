#!/usr/bin/env node
/**
 * Deterministic Stratified Property & Overlay Archetype Generator
 *
 * Mines the live Houston Building Atlas datasets under app/public/data/ to produce
 * 45 canonical, non-duplicated test archetypes across 15 equivalence classes (3 per class).
 *
 * Every equivalence class targets a distinct set of conditional UI cards, badges, tabs,
 * deep-link formats, and external archival URL templates in app/js/app.js and mapController.js.
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..");
const DATA_DIR = path.join(REPO_ROOT, "app", "public", "data");
const OUT_FILE = path.join(REPO_ROOT, "tests", "fixtures", "stratified_archetypes.json");

function readJson(filename) {
  return JSON.parse(fs.readFileSync(path.join(DATA_DIR, filename), "utf8"));
}

function geomCentroid(geom) {
  if (!geom || !geom.coordinates) return [null, null];
  if (geom.type === "Point") return [Number(geom.coordinates[0]), Number(geom.coordinates[1])];
  let ring = null;
  if (geom.type === "Polygon") ring = geom.coordinates[0];
  else if (geom.type === "MultiPolygon") ring = geom.coordinates[0]?.[0];
  else if (geom.type === "LineString") ring = geom.coordinates;
  else if (geom.type === "MultiLineString") ring = geom.coordinates[0];
  if (!Array.isArray(ring) || ring.length === 0) return [null, null];
  let sx = 0;
  let sy = 0;
  let n = 0;
  for (const pt of ring) {
    if (Array.isArray(pt) && Number.isFinite(Number(pt[0])) && Number.isFinite(Number(pt[1]))) {
      sx += Number(pt[0]);
      sy += Number(pt[1]);
      n++;
    }
  }
  return n > 0 ? [Number((sx / n).toFixed(6)), Number((sy / n).toFixed(6))] : [null, null];
}

function main() {
  const overlays = readJson("overlays.json");
  const buildingsFc = readJson("buildings.geojson");
  const curatedRaw = readJson("curated_overrides.json");
  const overrides = curatedRaw.overrides || curatedRaw;
  const permitAlerts = readJson("active_permit_alerts.json");
  const hahcTracker = readJson("hahc_coa_tracker.json");
  const citydirIndex = readJson("citydir_index.json");
  const deedCatalog = readJson("deed_restrictions_catalog.json");

  const buildings = buildingsFc.features || [];
  const byHcad = new Map();
  for (const feat of buildings) {
    const p = feat.properties || {};
    const h = String(p.hcad_num || "").trim();
    if (h && !byHcad.has(h)) {
      byHcad.set(h, feat);
    }
  }

  const usedKeys = new Set();
  const archetypes = [];

  function pickThree(classId, className, candidates, mapper, expectedUi) {
    const picked = [];
    for (const c of candidates) {
      const item = mapper(c);
      if (!item || !Number.isFinite(item.lng) || !Number.isFinite(item.lat)) continue;
      const uKey = `${item.targetType}:${item.overlayLayer || "building"}:${item.id || item.hcadNum || item.name}`;
      if (usedKeys.has(uKey)) continue;
      usedKeys.add(uKey);
      picked.push({
        archetypeId: `${classId}_${picked.length + 1}`,
        classId,
        className,
        ...item,
        name: String(item.name || "").replace(/\u2014/g, " - "),
        address: String(item.address || "").replace(/\u2014/g, " - "),
        expectedUi,
      });
      if (picked.length === 3) break;
    }
    if (picked.length < 3) {
      throw new Error(
        `Equivalence class '${classId}' (${className}) only found ${picked.length}/3 valid candidates.`
      );
    }
    archetypes.push(...picked);
  }

  // Class 1: COH Protected Landmark (PLM) with HPO Report PDF
  const plmLandmarks = (overlays.landmarks?.features || []).filter((f) => {
    const p = f.properties || {};
    return (
      p.protection_tier === "plm" &&
      /^\d{13}$/.test(String(p.hcad_num || p.USER_HCAD_NUM || "")) &&
      Boolean(p.report_pdf_url || p.landmark_report_url)
    );
  });
  // Prioritize Julia Ideson Building (0011470000001) first if present
  plmLandmarks.sort((a, b) => {
    const ha = String(a.properties?.hcad_num || a.properties?.USER_HCAD_NUM || "");
    const hb = String(b.properties?.hcad_num || b.properties?.USER_HCAD_NUM || "");
    if (ha === "0011470000001") return -1;
    if (hb === "0011470000001") return 1;
    return 0;
  });
  pickThree(
    "c01_plm",
    "COH Protected Landmark (PLM) with HPO Designation Report",
    plmLandmarks,
    (f) => {
      const p = f.properties || {};
      const [lng, lat] = geomCentroid(f.geometry);
      const hcadNum = String(p.hcad_num || p.USER_HCAD_NUM || "");
      return {
        targetType: "building",
        hcadNum,
        id: "",
        name: String(p.name || p.USER_LANDMARK || p.address || hcadNum),
        address: String(p.address || p.USER_ADDRESS || ""),
        lng,
        lat,
        deepLinkHash: `#hcad=${hcadNum}`,
      };
    },
    {
      mustHaveTabs: ["overview", "history", "permits", "hcad"],
      mustContainTextAny: ["Protected Landmark", "Permanent", "LEGAL PROTECTION"],
      mustHaveExternalLinkDomain: "houstontx.gov",
    }
  );

  // Class 2: Regular Landmark (LM) inside a COH Historic District (LM-in-HD)
  const lmInHd = (overlays.landmarks?.features || []).filter((f) => {
    const p = f.properties || {};
    return (
      p.protection_tier === "lm_in_hd" &&
      /^\d{13}$/.test(String(p.hcad_num || p.USER_HCAD_NUM || ""))
    );
  });
  pickThree(
    "c02_lm_in_hd",
    "Regular COH Landmark Inside COH Historic District (LM-in-HD)",
    lmInHd,
    (f) => {
      const p = f.properties || {};
      const [lng, lat] = geomCentroid(f.geometry);
      const hcadNum = String(p.hcad_num || p.USER_HCAD_NUM || "");
      return {
        targetType: "building",
        hcadNum,
        id: "",
        name: String(p.name || p.USER_LANDMARK || p.address || hcadNum),
        address: String(p.address || p.USER_ADDRESS || ""),
        lng,
        lat,
        deepLinkHash: `#hcad=${hcadNum}`,
      };
    },
    {
      mustHaveTabs: ["overview", "history", "permits", "hcad"],
      mustContainTextAny: ["Landmark", "LEGAL PROTECTION", "HAHC"],
    }
  );

  // Class 3: 90-Day Waiver Vulnerable Landmark (LM outside Historic District)
  const waiverLms = (overlays.landmarks?.features || []).filter((f) => {
    const p = f.properties || {};
    const tier = String(p.protection_tier || "");
    return (
      (tier === "waiver_lm" || tier === "lm_waiver_vulnerable") &&
      /^\d{13}$/.test(String(p.hcad_num || p.USER_HCAD_NUM || ""))
    );
  });
  waiverLms.sort((a, b) => {
    const ha = String(a.properties?.hcad_num || a.properties?.USER_HCAD_NUM || "");
    const hb = String(b.properties?.hcad_num || b.properties?.USER_HCAD_NUM || "");
    if (ha === "0010810000007") return -1;
    if (hb === "0010810000007") return 1;
    return 0;
  });
  pickThree(
    "c03_waiver_lm",
    "90-Day Waiver Vulnerable Landmark (LM Outside Historic District)",
    waiverLms,
    (f) => {
      const p = f.properties || {};
      const [lng, lat] = geomCentroid(f.geometry);
      const hcadNum = String(p.hcad_num || p.USER_HCAD_NUM || "");
      return {
        targetType: "building",
        hcadNum,
        id: "",
        name: String(p.name || p.USER_LANDMARK || p.address || hcadNum),
        address: String(p.address || p.USER_ADDRESS || ""),
        lng,
        lat,
        deepLinkHash: `#hcad=${hcadNum}`,
      };
    },
    {
      mustHaveTabs: ["overview", "history", "permits", "hcad"],
      mustContainTextAny: ["90-Day Waiver", "LEGAL PROTECTION"],
    }
  );

  // Class 4: COH Historic District Contributing + Active HAHC Agenda / House History Kit
  const activeHahcFilings = (hahcTracker.filings || []).filter(
    (fl) => fl.is_active_agenda && /^\d{13}$/.test(String(fl.hcad_num || "")) && Number.isFinite(fl.lng)
  );
  const c4Candidates = [];
  const walling = activeHahcFilings.find((fl) => fl.hcad_num === "0620820000016");
  if (walling) c4Candidates.push({ hcadNum: walling.hcad_num, name: walling.address, address: walling.address, lng: walling.lng, lat: walling.lat });
  // Dave's house 1127 Key St (Norhill Contributing + CityDir + UT PCL Sanborn 1924 Vol 2 Sheet 273)
  const keyFeat = byHcad.get("0621100000014");
  if (keyFeat) {
    const [klng, klat] = geomCentroid(keyFeat.geometry);
    c4Candidates.push({
      hcadNum: "0621100000014",
      name: "1127 KEY ST",
      address: "1127 KEY ST",
      lng: klng,
      lat: klat,
    });
  }
  for (const fl of activeHahcFilings) {
    c4Candidates.push({
      hcadNum: fl.hcad_num,
      name: fl.address,
      address: fl.address,
      lng: fl.lng,
      lat: fl.lat,
    });
  }
  pickThree(
    "c04_hd_contributing_hahc_kit",
    "COH Historic District Parcel with HAHC COA Precedents & House History Kit",
    c4Candidates,
    (c) => ({
      targetType: "building",
      hcadNum: c.hcadNum,
      id: "",
      name: c.name,
      address: c.address,
      lng: Number(c.lng),
      lat: Number(c.lat),
      deepLinkHash: `#hcad=${c.hcadNum}`,
    }),
    {
      mustHaveTabs: ["overview", "history", "permits", "hcad"],
      mustContainTextAny: ["HAHC COA PRECEDENTS", "House History", "Historic District"],
      mustHaveSelectorInPermits: ".hahc-district-precedents-card",
    }
  );

  // Class 5: COH Historic District Non-Contributing Modern Infill
  const nonContribBuildings = buildings.filter((f) => {
    const p = f.properties || {};
    return (
      p.historic_district &&
      p.historic_district !== "Outside Historic District" &&
      /non/i.test(String(p.contributing_status || p.bld_class_coh || "")) &&
      Number(p.year_built) >= 1995 &&
      /^\d{13}$/.test(String(p.hcad_num || ""))
    );
  });
  // Fallback if property field is named differently in buildings.geojson
  const nonContribFallback =
    nonContribBuildings.length >= 3
      ? nonContribBuildings
      : buildings.filter((f) => {
          const p = f.properties || {};
          return (
            p.historic_district &&
            p.historic_district !== "Outside Historic District" &&
            Number(p.year_built) >= 2005 &&
            /^\d{13}$/.test(String(p.hcad_num || ""))
          );
        });
  pickThree(
    "c05_hd_noncontributing_infill",
    "Post-1995 Infill Parcel Inside COH Historic District",
    nonContribFallback,
    (f) => {
      const p = f.properties || {};
      const [lng, lat] = geomCentroid(f.geometry);
      return {
        targetType: "building",
        hcadNum: String(p.hcad_num),
        id: String(p.id || ""),
        name: String(p.address || p.hcad_num),
        address: String(p.address || ""),
        lng,
        lat,
        deepLinkHash: `#hcad=${p.hcad_num}`,
      };
    },
    {
      mustHaveTabs: ["overview", "history", "permits", "hcad"],
      mustContainTextAny: ["HAHC COA PRECEDENTS", "Historic District"],
      mustHaveSelectorInPermits: ".hahc-district-precedents-card",
    }
  );

  // Class 6: 1915 Red Book Surviving Era Structure / Freedmen's Town Heritage
  const redBookSurviving = (overlays.red_book_1915?.features || []).filter((f) => {
    const p = f.properties || {};
    return p.survival_status === "surviving_era_structure" && /^\d{13}$/.test(String(p.matched_hcad_num || p.hcad_num || ""));
  });
  pickThree(
    "c06_red_book_surviving",
    "1915 Red Book of Houston Surviving Historic Structure",
    redBookSurviving,
    (f) => {
      const p = f.properties || {};
      const [lng, lat] = geomCentroid(f.geometry);
      const hcadNum = String(p.matched_hcad_num || p.hcad_num || "");
      return {
        targetType: "building",
        hcadNum,
        id: String(p.building_id || ""),
        name: String(p.name || p.person_name || p.modern_address || hcadNum),
        address: String(p.modern_address || p.historic_address_1915 || ""),
        lng,
        lat,
        deepLinkHash: `#hcad=${hcadNum}`,
      };
    },
    {
      mustHaveTabs: ["overview", "history", "permits", "hcad"],
      mustContainTextAny: ["Red Book", "1915"],
    }
  );

  // Class 7: 1915 Red Book Cleared / Freeway ROW Overlay Pin Dossier
  const redBookCleared = (overlays.red_book_1915?.features || []).filter((f) => {
    const p = f.properties || {};
    return p.survival_status === "cleared_or_row" && p.id;
  });
  pickThree(
    "c07_red_book_cleared_overlay",
    "1915 Red Book Cleared / Freeway Right-of-Way Overlay Dossier",
    redBookCleared,
    (f) => {
      const p = f.properties || {};
      const [lng, lat] = geomCentroid(f.geometry);
      return {
        targetType: "overlay",
        overlayLayer: "red_book_1915",
        id: String(p.id),
        hcadNum: "",
        name: String(p.name || p.person_name || p.id),
        address: String(p.historic_address_1915 || ""),
        lng,
        lat,
        deepLinkHash: `#bnd=red_book_1915:${encodeURIComponent(String(p.id))}`,
      };
    },
    {
      mustContainTextAny: ["1915 Red Book", "Red Book"],
      mustHaveExternalLinkDomain: "oclc.org",
    }
  );

  // Class 8: 1937 HOLC Redlining Zone Boundary Dossiers (Grades A, C, D)
  const holcFeatures = overlays.holc_1937_zones?.features || [];
  const holcPicked = [
    holcFeatures.find((f) => f.properties?.holc_grade === "D" || f.properties?.grade === "D"),
    holcFeatures.find((f) => f.properties?.holc_grade === "A" || f.properties?.grade === "A"),
    holcFeatures.find((f) => f.properties?.holc_grade === "C" || f.properties?.grade === "C"),
    ...holcFeatures,
  ].filter(Boolean);
  pickThree(
    "c08_holc_1937_zones",
    "1937 HOLC Redlining & HARC Heat Watch Equity Zone Dossier",
    holcPicked,
    (f) => {
      const p = f.properties || {};
      const [lng, lat] = geomCentroid(f.geometry);
      const id = String(p.id || p.zone_code || p.holc_id || p.name);
      return {
        targetType: "overlay",
        overlayLayer: "holc_1937_zones",
        id,
        hcadNum: "",
        name: String(p.name || p.zone_code || id),
        address: "",
        lng,
        lat,
        deepLinkHash: `#bnd=holc_1937_zones:${encodeURIComponent(id)}`,
      };
    },
    {
      mustContainTextAny: ["1937 HOLC", "NARA"],
    }
  );

  // Class 9: NRHP Individual Properties (Extant + Demolished 'Gone')
  const nrhpFeatures = overlays.nrhp_properties?.features || [];
  const nrhpGone = nrhpFeatures.filter((f) => f.properties?.is_demolished_nrhp);
  const nrhpExtant = nrhpFeatures.filter((f) => !f.properties?.is_demolished_nrhp && f.properties?.nomination_pdf_url);
  pickThree(
    "c09_nrhp_individual",
    "National Register (NRHP) Individual Properties (Extant & Demolished)",
    [...nrhpExtant.slice(0, 2), ...nrhpGone.slice(0, 2), ...nrhpExtant],
    (f) => {
      const p = f.properties || {};
      const [lng, lat] = geomCentroid(f.geometry);
      const id = String(p.id || p.refnum || p.name);
      return {
        targetType: "overlay",
        overlayLayer: "nrhp_properties",
        id,
        hcadNum: String(p.hcad_num || ""),
        name: String(p.name || id),
        address: String(p.address || ""),
        lng,
        lat,
        deepLinkHash: `#bnd=nrhp_properties:${encodeURIComponent(id)}`,
      };
    },
    {
      mustContainTextAny: ["National Register", "NRHP"],
      mustHaveExternalLinkDomain: "atlas.thc.texas.gov",
    }
  );

  // Class 10: 2019 Surveyed Potential Historic Districts & Cultural Arts Districts
  const potHds = (overlays.potential_historic_districts?.features || []).slice(0, 2).map((f) => ({
    f,
    layer: "potential_historic_districts",
  }));
  const cultDists = (overlays.cultural_districts?.features || []).slice(0, 2).map((f) => ({
    f,
    layer: "cultural_districts",
  }));
  pickThree(
    "c10_potential_and_cultural_districts",
    "2019 Potential Historic Districts & Cultural Arts Districts",
    [...potHds, ...cultDists],
    ({ f, layer }) => {
      const p = f.properties || {};
      const [lng, lat] = geomCentroid(f.geometry);
      const id = String(p.id || p.name);
      return {
        targetType: "overlay",
        overlayLayer: layer,
        id,
        hcadNum: "",
        name: String(p.name || id),
        address: "",
        lng,
        lat,
        deepLinkHash: `#bnd=${layer}:${encodeURIComponent(id)}`,
      };
    },
    {
      mustContainTextAny: ["Historic", "District", "Structures"],
    }
  );

  // Class 11: Chapter 42 Land Use Protections (SMLS / SMBL) & Deed-Restricted Subdivisions
  const lupFeatures = overlays.land_use_protections?.features || [];
  const smlsFeat = lupFeatures.find((f) => f.properties?.protection_type === "smls") || lupFeatures[0];
  const smblFeat = lupFeatures.find((f) => f.properties?.protection_type === "smbl") || lupFeatures[1];
  const ch42Feats = [smlsFeat, smblFeat, ...lupFeatures.slice(0, 3)]
    .filter(Boolean)
    .map((f) => ({ f, layer: "land_use_protections" }));
  const deedNamesLower = new Set(Object.keys(deedCatalog.by_name || {}).map((k) => k.toLowerCase()));
  const deedSubFeats = (overlays.platted_subdivisions?.features || [])
    .filter((f) => {
      const p = f.properties || {};
      return (
        p.has_deed_pdf ||
        deedNamesLower.has(String(p.name || "").toLowerCase()) ||
        deedNamesLower.has(String(p.full_name || "").toLowerCase())
      );
    })
    .slice(0, 3)
    .map((f) => ({ f, layer: "platted_subdivisions" }));
  pickThree(
    "c11_deeds_and_ch42",
    "Deed-Restricted Subdivisions & Chapter 42 SMLS/SMBL Blocks",
    [...deedSubFeats.slice(0, 1), ...ch42Feats],
    ({ f, layer }) => {
      const p = f.properties || {};
      const [lng, lat] = geomCentroid(f.geometry);
      const id = String(p.id || p.name);
      return {
        targetType: "overlay",
        overlayLayer: layer,
        id,
        hcadNum: "",
        name: String(p.name || id),
        address: "",
        lng,
        lat,
        deepLinkHash: `#bnd=${layer}:${encodeURIComponent(id)}`,
      };
    },
    {
      mustContainTextAny: ["Subdivision", "Lot", "Ordinance", "Deed"],
    }
  );

  // Class 12: Unprotected Pre-1935 Tear-Down Risk Parcels Outside Historic Districts
  const unprotectedHistoric = buildings.filter((f) => {
    const p = f.properties || {};
    const yr = Number(p.year_built);
    const hd = String(p.historic_district || "");
    return (
      yr >= 1890 &&
      yr <= 1935 &&
      (!hd || hd === "Outside Historic District") &&
      !p.landmark_name &&
      !p.landmark_type &&
      /^\d{13}$/.test(String(p.hcad_num || ""))
    );
  });
  pickThree(
    "c12_unprotected_pre1935",
    "Unprotected Pre-1935 Inner-Core Parcel (Tear-Down Vulnerability)",
    unprotectedHistoric,
    (f) => {
      const p = f.properties || {};
      const [lng, lat] = geomCentroid(f.geometry);
      return {
        targetType: "building",
        hcadNum: String(p.hcad_num),
        id: String(p.id || ""),
        name: String(p.address || p.hcad_num),
        address: String(p.address || ""),
        lng,
        lat,
        deepLinkHash: `#hcad=${p.hcad_num}`,
      };
    },
    {
      mustHaveTabs: ["overview", "history", "permits", "hcad"],
      mustContainTextAny: ["Outside Historic District", "LEGAL PROTECTION"],
    }
  );

  // Class 13: Active Demolition / DSHS Asbestos Permit Watch Alerts
  const alertsList = (permitAlerts.alerts || []).filter(
    (a) => /^\d{13}$/.test(String(a.hcad_num || "")) && Number.isFinite(Number(a.lng ?? a.lon)) && Number.isFinite(Number(a.lat))
  );
  pickThree(
    "c13_active_permit_watch",
    "Active Demolition, Sewer Disconnect & DSHS Asbestos Watch Parcel",
    alertsList,
    (a) => ({
      targetType: "building",
      hcadNum: String(a.hcad_num),
      id: String(a.building_id || ""),
      name: String(a.building_name || a.address || a.hcad_num),
      address: String(a.address || ""),
      lng: Number(a.lng ?? a.lon),
      lat: Number(a.lat),
      deepLinkHash: `#hcad=${a.hcad_num}`,
    }),
    {
      mustHaveTabs: ["overview", "history", "permits", "hcad"],
      mustContainTextAny: ["Permit", "LEGAL PROTECTION"],
    }
  );

  // Class 14: Multi-Building Tax-Exempt Institutional Campus Composite ('#') Overrides
  const compositeEntries = Object.entries(overrides).filter(([k, rec]) => {
    if (!k.includes("#") || k.includes("#aux_") || k.includes("#fp_")) return false;
    const [lng, lat] = geomCentroid(rec?.geometry);
    return Number.isFinite(lng) && Number.isFinite(lat) && Number(rec?.year_built) >= 1840;
  });
  pickThree(
    "c14_campus_composite_override",
    "Multi-Building Institutional Campus Sub-Structure (Composite '#' ID)",
    compositeEntries,
    ([ovKey, rec]) => {
      const [lng, lat] = geomCentroid(rec.geometry);
      return {
        targetType: "building",
        hcadNum: String(rec.hcad_num || ovKey.split("#")[0]),
        id: ovKey,
        name: String(rec.building_name || rec.address || ovKey),
        address: String(rec.address || ""),
        lng,
        lat,
        deepLinkHash: `#id=${encodeURIComponent(ovKey)}`,
      };
    },
    {
      mustHaveTabs: ["overview", "history", "permits", "hcad"],
      mustContainTextAny: ["Built", "LEGAL PROTECTION"],
    }
  );

  // Class 15: Lost Houston Demolished Architectural Landmarks (Sanborn Linked)
  const lostFeats = (overlays.lost_houston?.features || []).filter(
    (f) => f.properties?.id && f.properties?.loc_volume_id
  );
  pickThree(
    "c15_lost_houston_sanborn",
    "Lost Houston Demolished Landmark with Sanborn Atlas Plate",
    lostFeats,
    (f) => {
      const p = f.properties || {};
      const [lng, lat] = geomCentroid(f.geometry);
      const id = String(p.id);
      return {
        targetType: "overlay",
        overlayLayer: "lost_houston",
        id,
        hcadNum: String(p.modern_hcad_num || ""),
        name: String(p.name || id),
        address: String(p.historic_address || p.address || ""),
        lng,
        lat,
        deepLinkHash: `#bnd=lost_houston:${encodeURIComponent(id)}`,
      };
    },
    {
      mustContainTextAny: ["Demolished", "Sanborn"],
    }
  );

  fs.mkdirSync(path.dirname(OUT_FILE), { recursive: true });
  const payload = {
    generatedAt: "2026-10-25",
    totalClasses: 15,
    totalArchetypes: archetypes.length,
    archetypes,
  };
  fs.writeFileSync(OUT_FILE, JSON.stringify(payload, null, 2) + "\n", "utf8");
  console.log(
    `Generated ${archetypes.length} stratified archetypes across 15 equivalence classes -> ${path.relative(
      REPO_ROOT,
      OUT_FILE
    )}`
  );
}

main();
