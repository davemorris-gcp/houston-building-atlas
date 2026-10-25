import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  ARCHITECT_AND_STYLE_BIOGRAPHIES,
  CITY_HISTORY_CHAPTERS,
} from "../../app/js/guidedChaptersAndPortfolios.js";
import { CURATED_TOURS } from "../../app/js/palettes.js";
import {
  classifyFieldEvidentiaryTier,
  isClaimSuppressed,
  parseGlobalRejectionsFromSheetRows,
  submitQuickFlagReport,
} from "../../app/js/curatedEdits.js";
import {
  clearRegisteredPhotoExclusions,
  evaluateCommonsCandidate,
  isPhotoGloballyRejected,
  registerGloballyRejectedPhotos,
  registerKnownLandmarksForPhotoExclusion,
} from "../../app/js/photoService.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..", "..");
const DATA_DIR = path.join(REPO_ROOT, "app", "public", "data");

function readDataJson(name) {
  const fullPath = path.join(DATA_DIR, name);
  assert.ok(fs.existsSync(fullPath), `Expected data file to exist: ${fullPath}`);
  return JSON.parse(fs.readFileSync(fullPath, "utf8"));
}

test("Phase A: Guided Chapters, Portfolio Biographies, Walking Tours, and Deed Catalog carry primary-source citations and verified Norhill Hogg/Varner attribution", () => {
  assert.equal(CITY_HISTORY_CHAPTERS.length, 6);
  for (const ch of CITY_HISTORY_CHAPTERS) {
    assert.ok(
      Array.isArray(ch.citations) && ch.citations.length >= 1,
      `Chapter ${ch.id} must carry >= 1 primary archival citation`
    );
  }

  const ch3 = CITY_HISTORY_CHAPTERS.find((c) => c.id === "streetcar_suburbs_and_deeds");
  assert.ok(ch3, "Expected Chapter 3 streetcar_suburbs_and_deeds");
  assert.ok(
    !ch3.narrative.includes("Russell Brown's Norhill"),
    "Chapter 3 must not misattribute Norhill subdivision development to Russell Brown"
  );
  assert.ok(
    /Will Hogg/i.test(ch3.narrative) && /Varner Realty/i.test(ch3.narrative),
    "Chapter 3 must credit Will Hogg and Varner Realty Co. for developing Norhill"
  );

  const bioEntries = Object.entries(ARCHITECT_AND_STYLE_BIOGRAPHIES);
  assert.ok(bioEntries.length >= 27, `Expected >= 27 portfolio biographies, got ${bioEntries.length}`);
  for (const [key, bio] of bioEntries) {
    assert.ok(
      Array.isArray(bio.citations) && bio.citations.length >= 1,
      `Portfolio biography '${key}' must carry >= 1 primary archival citation`
    );
  }

  assert.ok(CURATED_TOURS.length >= 9, `Expected >= 9 curated walking tours, got ${CURATED_TOURS.length}`);
  for (const tour of CURATED_TOURS) {
    assert.ok(
      Array.isArray(tour.citations) && tour.citations.length >= 1,
      `Curated tour '${tour.id}' must carry >= 1 primary archival citation`
    );
  }

  const deedCatalog = readDataJson("deed_restrictions_catalog.json");
  const subEntries = Array.isArray(deedCatalog.subdivisions)
    ? deedCatalog.subdivisions
    : Object.values(deedCatalog.subdivisions || {});
  assert.ok(subEntries.length >= 30, `Expected >= 30 deed restriction entries, got ${subEntries.length}`);
  for (const sub of subEntries) {
    assert.ok(
      Array.isArray(sub.citations) && sub.citations.length >= 1,
      `Deed catalog entry '${sub.subdivision_name}' must carry >= 1 primary citation`
    );
  }
});

test("Phase A & C: Lost Houston excludes extant landmarks and building_photos.json contains zero favicons, TGCA text tables, or demolished replacement photos", () => {
  const overlays = readDataJson("overlays.json");
  const lostFeatures = overlays.lost_houston?.features || [];
  assert.equal(
    lostFeatures.length,
    74,
    `Expected 74 verified demolished Lost Houston structures (66 surveyed + 8 Red Book), got ${lostFeatures.length}`
  );

  const extantFalsePositives = [
    /^paul building\b/i,
    /^scanlan building\b/i,
    /^carter building\b/i,
    /^humble oil building\b/i,
    /^merchants\s*&\s*manufacturers building\b/i,
    /^jefferson davis hospital \(1924/i,
  ];

  for (const feat of lostFeatures) {
    const nm = String(feat.properties?.name || "");
    for (const pat of extantFalsePositives) {
      assert.ok(!pat.test(nm), `Extant building '${nm}' must not appear in Lost Houston`);
    }
    assert.ok(
      Array.isArray(feat.properties?.citations) && feat.properties.citations.length >= 1,
      `Lost Houston entry '${nm}' must carry >= 1 primary archival citation`
    );
  }

  // Verify Courtlandt Place spelling normalization across Historic Districts
  const hdNames = (overlays.historic_districts?.features || []).map((f) => f.properties?.name || "");
  assert.ok(
    !hdNames.some((n) => /Courtland Place/i.test(n)),
    "Historic Districts must use canonical 'Courtlandt Place' spelling"
  );

  const photosData = readDataJson("building_photos.json");
  const allJsonText = JSON.stringify(photosData);
  assert.ok(
    !/android-chrome/i.test(allJsonText),
    "building_photos.json must not contain scraped android-chrome favicons"
  );
  assert.ok(
    !/1059_Kirby\.jpg/i.test(allJsonText),
    "building_photos.json must not attach demolished 1059 Kirby photo to modern replacement"
  );

  // Verify HPL p17006coll12 pointers in buildings map only to genuine Russell Brown plates, never TGCA text tables 515..529
  for (const [bid, list] of Object.entries(photosData.buildings || {})) {
    for (const item of list) {
      const url = String(item.image_url || item.url || "");
      const m = url.match(/p17006coll12\/id\/(\d+)/);
      if (m) {
        const ptr = Number(m[1]);
        assert.ok(
          ptr < 515 || ptr > 529,
          `Building '${bid}' references TGCA text-only table pointer ${ptr}: ${url}`
        );
      }
    }
  }

  // Verify 310 Main St (Sweeney & Coombs Building) is bound to bld_006815 / 0010330000018
  const sweeneyPhotos = (photosData.photos || []).filter((p) =>
    /Sweeney.*Building/i.test(p.landmark_name || "")
  );
  assert.ok(sweeneyPhotos.length >= 1, "Expected curated photo for Sweeney & Coombs Building (310 Main St)");
  for (const sp of sweeneyPhotos) {
    assert.equal(sp.building_id, "bld_006815");
    assert.equal(sp.hcad_num, "0010330000018");
  }
});

test("Phase B: 4-Tier Evidentiary Provenance classifier, HCAD rounded-decade contradiction flags, and 1-click claim/photo suppression", async () => {
  const tier1 = classifyFieldEvidentiaryTier({
    field: "year_built",
    value: "1911",
    sourceLabel: "COH Landmark Designation Report",
    isCuratedOverride: true,
  });
  assert.equal(tier1.tier, 1);
  assert.equal(tier1.requiresFlagBadge, false);

  const tier2 = classifyFieldEvidentiaryTier({
    field: "architect",
    value: "Alfred C. Finn",
    sourceLabel: "AIA Houston Architectural Guide",
  });
  assert.equal(tier2.tier, 2);
  assert.equal(tier2.requiresFlagBadge, false);

  const tier3Rounded = classifyFieldEvidentiaryTier({
    field: "year_built",
    value: "1920",
    sourceLabel: "HCAD Appraisal Roll - Rounded Decade Estimate (unverified exact year)",
    isRoundedHcadDecade: true,
    isCityDirContradicted: false,
  });
  assert.equal(tier3Rounded.tier, 3);
  assert.equal(tier3Rounded.shortBadge, "HCAD Est.");
  assert.equal(tier3Rounded.isContradicted, false);

  const tier3Contradicted = classifyFieldEvidentiaryTier({
    field: "year_built",
    value: "1920",
    sourceLabel: "HCAD Appraisal Roll - Rounded Decade Estimate (unverified exact year)",
    isRoundedHcadDecade: true,
    isCityDirContradicted: true,
  });
  assert.equal(tier3Contradicted.tier, 3);
  assert.equal(tier3Contradicted.isContradicted, true);
  assert.match(tier3Contradicted.shortBadge, /Contradicted by 1926 Dir\./);

  const tier4Haif = classifyFieldEvidentiaryTier({
    field: "building_name",
    value: "Speculative Condos Thread",
    sourceLabel: "Houston Architecture Forum (HAIF)",
  });
  assert.equal(tier4Haif.tier, 4);
  assert.equal(tier4Haif.requiresFlagBadge, true);

  // Test global Approved_Edits rejection parsing & suppression
  const globalRej = parseGlobalRejectionsFromSheetRows([
    {
      status: "Approved",
      hcad_num: "0010560000002",
      source_type: "PHOTO_MISATTRIBUTION_FLAG",
      photo_url: "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Wrong_Neighbor_Tower.jpg/960px-Wrong_Neighbor_Tower.jpg",
      source_citation: "[1-CLICK PHOTO FLAG: Wrong Building]",
    },
    {
      status: "Approved",
      hcad_num: "0480730000005",
      source_type: "CLAIM_DISPUTE_FLAG",
      source_citation: "[DISPUTED FIELD: building_name] | [DISPUTED VALUE: Wrong Forum Name]",
    },
    {
      status: "Pending",
      hcad_num: "0480730000005",
      source_type: "CLAIM_DISPUTE_FLAG",
      source_citation: "[DISPUTED FIELD: architect] | [DISPUTED VALUE: Unapproved Pending Dispute]",
    },
  ]);

  assert.equal(globalRej.rejectedPhotos.length, 1);
  assert.equal(globalRej.rejectedClaims.length, 1);
  assert.equal(
    isClaimSuppressed(
      { hcadNum: "0480730000005", field: "building_name", value: "Wrong Forum Name" },
      globalRej
    ),
    true
  );
  assert.equal(
    isClaimSuppressed(
      { hcadNum: "0480730000005", field: "architect", value: "Unapproved Pending Dispute" },
      globalRej
    ),
    false
  );

  // Verify quick-flag report payload formatting without network call
  const flagRes = await submitQuickFlagReport(
    {
      flagType: "narrative",
      context_id: "chapter_streetcars_heights_and_early_suburbs",
      context_title: "Chapter 3: Streetcars, Heights & Early Garden Suburbs",
      disputed_value: "Sample narrative test check",
    },
    ""
  );
  assert.equal(flagRes.ok, true);
  assert.equal(flagRes.record.source_type, "NARRATIVE_ERROR_FLAG");

  const auditSummary = readDataJson("historical_audit_summary.json");
  assert.ok(
    auditSummary.historic_core_hcad_rounded_decade_estimates >= 2500,
    "Expected historical_audit_summary.json to quantify >= 2,500 Historic Core HCAD rounded-decade estimates"
  );
  assert.ok(
    auditSummary.total_queued_contradictions >= 2000,
    "Expected historical_audit_summary.json to record >= 2,000 cross-dataset audit findings"
  );
});

test("Phase C: evaluateCommonsCandidate blocks wrong street addresses, neighboring landmarks, non-building subjects, and globally rejected photos", () => {
  clearRegisteredPhotoExclusions();
  registerKnownLandmarksForPhotoExclusion([
    {
      hcad_num: "0010820000001",
      landmark_name: "JPMorgan Chase Building (Gulf Building)",
      address: "712 MAIN ST",
    },
    {
      hcad_num: "0010330000018",
      landmark_name: "Sweeney and Coombs Building",
      address: "310 MAIN ST",
    },
  ]);
  registerGloballyRejectedPhotos([
    {
      hcadNum: "0010330000005",
      photoUrl: "https://upload.wikimedia.org/wikipedia/commons/9/99/Misattributed_Corner_View.jpg",
    },
  ]);

  assert.equal(
    isPhotoGloballyRejected(
      "https://upload.wikimedia.org/wikipedia/commons/thumb/9/99/Misattributed_Corner_View.jpg/960px-Misattributed_Corner_View.jpg",
      "0010330000005"
    ),
    true,
    "Expected thumbnail variant of globally rejected filename to be blocked on matching HCAD"
  );

  // 1. Reject photo whose caption/title explicitly names a different street number on the same block
  const wrongNumberPage = {
    title: "File:340 Main Street Historic Facade Houston.jpg",
    imageinfo: [
      {
        url: "https://upload.wikimedia.org/wikipedia/commons/1/11/340_Main_Street_Historic_Facade_Houston.jpg",
        thumburl: "https://upload.wikimedia.org/wikipedia/commons/thumb/1/11/340_Main_Street_Historic_Facade_Houston.jpg/960px-340_Main_Street_Historic_Facade_Houston.jpg",
        extmetadata: {
          ImageDescription: { value: "Historic brick commercial building at 340 Main Street, Houston, Texas." },
          Categories: { value: "Commercial buildings in Houston" },
        },
      },
    ],
  };
  const resWrongNum = evaluateCommonsCandidate(wrongNumberPage, {
    hcadNum: "0010330000005",
    landmarkName: "Stuart Building",
    address: "304 MAIN ST",
    sourceType: "geosearch",
  });
  assert.equal(resWrongNum.usable, false);
  assert.equal(resWrongNum.rejectReason, "conflicting_street_address");

  // 2. Reject photo whose title depicts a known neighboring landmark on a different parcel
  const neighborLandmarkPage = {
    title: "File:Sweeney and Coombs Building Turret Houston.jpg",
    imageinfo: [
      {
        url: "https://upload.wikimedia.org/wikipedia/commons/2/22/Sweeney_and_Coombs_Building_Turret_Houston.jpg",
        thumburl: "https://upload.wikimedia.org/wikipedia/commons/thumb/2/22/Sweeney_and_Coombs_Building_Turret_Houston.jpg/960px-Sweeney_and_Coombs_Building_Turret_Houston.jpg",
        extmetadata: {
          ImageDescription: { value: "Corner turret architecture in Downtown Houston." },
          Categories: { value: "Historic buildings in Houston" },
        },
      },
    ],
  };
  const resNeighbor = evaluateCommonsCandidate(neighborLandmarkPage, {
    hcadNum: "0010330000005",
    landmarkName: "Stuart Building",
    address: "304 MAIN ST",
    sourceType: "geosearch",
  });
  assert.equal(resNeighbor.usable, false);
  assert.equal(resNeighbor.rejectReason, "depicts_different_landmark");

  // 3. Reject globally moderator-flagged photo URL
  const globallyFlaggedPage = {
    title: "File:Misattributed Corner View.jpg",
    imageinfo: [
      {
        url: "https://upload.wikimedia.org/wikipedia/commons/9/99/Misattributed_Corner_View.jpg",
        thumburl: "https://upload.wikimedia.org/wikipedia/commons/thumb/9/99/Misattributed_Corner_View.jpg/960px-Misattributed_Corner_View.jpg",
        extmetadata: {
          ImageDescription: { value: "Historic brick building exterior in Houston." },
          Categories: { value: "Buildings in Houston" },
        },
      },
    ],
  };
  const resFlagged = evaluateCommonsCandidate(globallyFlaggedPage, {
    hcadNum: "0010330000005",
    landmarkName: "Stuart Building",
    address: "304 MAIN ST",
    sourceType: "geosearch",
  });
  assert.equal(resFlagged.usable, false);
  assert.equal(resFlagged.rejectReason, "hidden_or_rejected");

  // 4. Accept genuine architectural photo matching the target landmark & address
  const validPage = {
    title: "File:Sweeney and Coombs Building 310 Main Street Houston.jpg",
    imageinfo: [
      {
        url: "https://upload.wikimedia.org/wikipedia/commons/3/33/Sweeney_and_Coombs_Building_310_Main_Street_Houston.jpg",
        thumburl: "https://upload.wikimedia.org/wikipedia/commons/thumb/3/33/Sweeney_and_Coombs_Building_310_Main_Street_Houston.jpg/960px-Sweeney_and_Coombs_Building_310_Main_Street_Houston.jpg",
        extmetadata: {
          ImageDescription: { value: "Exterior facade of the Sweeney and Coombs Building at 310 Main Street, Houston." },
          Categories: { value: "Historic buildings in Houston | Victorian architecture in Texas" },
        },
      },
    ],
  };
  const resValid = evaluateCommonsCandidate(validPage, {
    hcadNum: "0010330000018",
    landmarkName: "Sweeney and Coombs Building",
    address: "310 MAIN ST",
    sourceType: "search",
  });
  assert.equal(resValid.usable, true);
  assert.ok(resValid.score > 0);

  clearRegisteredPhotoExclusions();
});
