import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "../..");
const DATA_DIR = path.join(REPO_ROOT, "app", "public", "data");
const ACTIVE_VER = String(
  JSON.parse(fs.readFileSync(path.join(DATA_DIR, "pmtiles_manifest.json"), "utf8"))
    .cache_bust || ""
).trim();

const {
  VALID_PERMIT_CAT_CODES,
  buildInspectorPermitsBodyHtml,
  decodeAppraiserNote,
  escapePermitHtml,
  explainCohWatchPermit,
  explainDshsNotification,
  explainExtraFeatureDesc,
  explainMultiAgencyFiling,
  explainPermitRecord,
  explainWatchlistCheckText,
  formatHcadInspectionDate,
  normalizePermitSourceLabel,
} = await import(`../../app/js/permitPlainEnglish.js?v=${ACTIVE_VER}`);
const {
  DEFAULT_FILTER_STATE,
  VALID_INSPECTOR_TABS,
  buildShareableUrl,
  extractUrlInspectorTab,
  parseHashToState,
  serializeStateToHash,
} = await import(`../../app/js/filterStore.js?v=${ACTIVE_VER}`);
const {
  sanitizeActivityEventForRender,
} = await import(`../../app/js/activityLogHelpers.js?v=${ACTIVE_VER}`);

const UNI_HYPHEN = String.fromCodePoint(0x2010);
const EN_DASH = String.fromCodePoint(0x2013);
const EM_DASH = String.fromCodePoint(0x2014);

function hasForbiddenDash(value) {
  const s = String(value ?? "");
  return s.includes(UNI_HYPHEN) || s.includes(EN_DASH) || s.includes(EM_DASH);
}

test("UT-M13-1: escapePermitHtml and normalizePermitSourceLabel enforce HTML escaping and case-insensitive p.src normalization", () => {
  assert.equal(escapePermitHtml(null), "");
  assert.equal(escapePermitHtml(undefined), "");
  assert.equal(escapePermitHtml(""), "");
  assert.equal(escapePermitHtml(1920), "1920");
  assert.equal(
    escapePermitHtml(`<img src=x onerror="alert('xss')">&`),
    "&lt;img src=x onerror=&quot;alert(&#39;xss&#39;)&quot;&gt;&amp;"
  );

  assert.equal(normalizePermitSourceLabel("tx_dshs"), "TX DSHS Notification");
  assert.equal(normalizePermitSourceLabel("TX_DSHS"), "TX DSHS Notification");
  assert.equal(normalizePermitSourceLabel("  dshs "), "TX DSHS Notification");
  assert.equal(normalizePermitSourceLabel("coh_live"), "COH Live Permit");
  assert.equal(normalizePermitSourceLabel("COH_LIVE"), "COH Live Permit");
  assert.equal(normalizePermitSourceLabel("coh_weekly"), "COH Live Permit");
  assert.equal(normalizePermitSourceLabel("COH_WEEKLY"), "COH Live Permit");
  assert.equal(normalizePermitSourceLabel("coh"), "COH Live Permit");
  assert.equal(normalizePermitSourceLabel("hcad_cama"), "");
  assert.equal(normalizePermitSourceLabel(""), "");
  assert.equal(normalizePermitSourceLabel(null), "");
});

test("UT-M13-2: formatHcadInspectionDate formats unambiguous dates while preserving ambiguous 2-part tokens without inspection prefixes", () => {
  assert.equal(formatHcadInspectionDate("4/1/26", true), "Apr 1, 2026");
  assert.equal(formatHcadInspectionDate("11/22/24", false), "Nov 22, 2024");
  assert.equal(formatHcadInspectionDate("9/5/98", true), "Sep 5, 1998");
  assert.equal(formatHcadInspectionDate("1/15/2021", false), "Jan 15, 2021");
  assert.equal(formatHcadInspectionDate("6/2019", false), "Jun 2019");
  assert.equal(formatHcadInspectionDate("12/15", true), "Dec 2015");
  assert.equal(formatHcadInspectionDate("12/15", false), "12/15");
  assert.equal(formatHcadInspectionDate("13/40/2022", true), "13/40/2022");
  assert.equal(formatHcadInspectionDate("", true), "");
});

test("UT-M13-3: decodeAppraiserNote handles multi-date splits, preposition-guarded dates, FR prefix vs Fair grade, PO Box, street AVE, subareas, and plain notes", () => {
  // Given a multi-inspection HCAD appraiser note (1016 Algregg St, 0620950000004)
  const algreggRaw =
    "4/1/26 NC: WORK DOES NOT APPEAR TO BE DONE @ TOI    11/22/24 FR: DWG WELL MAINTAINED, KEPT CDU @ GD";
  const algregg = decodeAppraiserNote(algreggRaw);

  assert.equal(algregg.hasShorthand, true);
  assert.equal(algregg.raw, algreggRaw);
  assert.equal(algregg.entries.length, 2);
  assert.deepEqual(algregg.entries[0], {
    date: "Apr 1, 2026",
    kind: "Permit check",
    text: "Work does not appear to be done at time of inspection.",
  });
  assert.deepEqual(algregg.entries[1], {
    date: "Nov 22, 2024",
    kind: "Field review",
    text: "Main dwelling well maintained, kept condition rating at Good.",
  });

  // Given preposition-guarded dates that must stay 1 single entry
  const asOfRes = decodeAppraiserNote("AS OF 1/1/2022 DWG REMODELED PER MLS");
  assert.equal(asOfRes.hasShorthand, true);
  assert.equal(asOfRes.entries.length, 1);
  assert.equal(asOfRes.entries[0].date, "");
  assert.equal(
    asOfRes.entries[0].text,
    "As of 1/1/2022 main dwelling remodeled per real-estate listing (MLS)"
  );

  const priorToRes = decodeAppraiserNote("DET GAR DEMOED PRIOR TO 3/15/2021 @ TOI");
  assert.equal(priorToRes.entries.length, 1);
  assert.equal(
    priorToRes.entries[0].text,
    "Detached garage demolished prior to 3/15/2021 at time of inspection."
  );

  // Given context-aware FR: inspection prefix vs Fair condition grade + hyphen-joined dates
  const frMixed = decodeAppraiserNote(
    "12/15-N/C-NO VISIBLE DAMAGE, CDU FROM PR TO FR.    9/5/13-FR RESKETCHED DWG, KEPT CDU @ FR"
  );
  assert.equal(frMixed.entries.length, 2);
  assert.deepEqual(frMixed.entries[0], {
    date: "Dec 2015",
    kind: "Permit check",
    text: "No visible damage, condition rating from Poor to Fair.",
  });
  assert.deepEqual(frMixed.entries[1], {
    date: "Sep 5, 2013",
    kind: "Field review",
    text: "Resketched main dwelling, kept condition rating at Fair.",
  });

  // Given PO Box and street AVE protection alongside P/O and CDU @ AVE shorthand
  const poBoxNote = decodeAppraiserNote(
    "1/12/11-YEAR BUILT PER P/O INSURANCE CO. P/O USES PO BOX 1234."
  );
  assert.equal(poBoxNote.entries.length, 1);
  assert.equal(poBoxNote.entries[0].date, "Jan 12, 2011");
  assert.equal(
    poBoxNote.entries[0].text,
    "Year built per property owner insurance co. property owner uses PO Box 1234."
  );

  const aveProtected = decodeAppraiserNote(
    "4/1/26 FR: ENTRANCE ON TEXAS AVE & @ AVE H, ADJ CDU FROM FAIR TO AVE//"
  );
  assert.equal(aveProtected.entries.length, 1);
  assert.equal(aveProtected.entries[0].kind, "Field review");
  assert.equal(
    aveProtected.entries[0].text,
    "Entrance on texas ave & @ ave h, adjusted condition rating from Fair to Average."
  );

  // Given STI HAD legacy sketch and 7-digit commercial BAS square footage
  const stiSketch = decodeAppraiserNote("STI HAD: BAS(1452) OFP(120)");
  assert.equal(stiSketch.entries.length, 1);
  assert.deepEqual(stiSketch.entries[0], {
    date: "",
    kind: "Legacy sketch",
    text: "Ground-floor living area (1,452 sq ft) open frame porch (120 sq ft)",
  });

  const towerSketch = decodeAppraiserNote(
    "FIRST CITY TOWER NOTE: AS OF 12/16/16 BAS(1365801)."
  );
  assert.equal(towerSketch.entries.length, 1);
  assert.ok(
    towerSketch.entries[0].text.includes("ground-floor living area (1,365,801 sq ft)")
  );

  // Dateless clauses starting with DR / MAINT must not fabricate inspection headers
  const datelessDr = decodeAppraiserNote("DR AND KIT RMDL @ TOI");
  assert.equal(datelessDr.hasShorthand, true);
  assert.deepEqual(datelessDr.entries, [
    {
      date: "",
      kind: "",
      text: "Dr and kitchen remodeled at time of inspection.",
    },
  ]);

  const datelessMaint = decodeAppraiserNote("MAINT NEEDED ON ROOF & SIDING, CDU @ FR");
  assert.equal(datelessMaint.hasShorthand, true);
  assert.deepEqual(datelessMaint.entries, [
    {
      date: "",
      kind: "",
      text: "Maint needed on roof & siding, condition rating at Fair.",
    },
  ]);

  // Semicolon and // boundaries before undated inspection prefixes split cleanly
  const multiDelim = decodeAppraiserNote(
    "4/1/26 NC: NO WORK @ TOI; FR: DWG WELL MAINTAINED // O/R: KEPT CDU @ GD"
  );
  assert.equal(multiDelim.entries.length, 3);
  assert.deepEqual(multiDelim.entries[0], {
    date: "Apr 1, 2026",
    kind: "Permit check",
    text: "No work at time of inspection.",
  });
  assert.deepEqual(multiDelim.entries[1], {
    date: "",
    kind: "Field review",
    text: "Main dwelling well maintained.",
  });
  assert.deepEqual(multiDelim.entries[2], {
    date: "",
    kind: "Office review",
    text: "Kept condition rating at Good.",
  });

  // Highway @ US <num>, @ PR <num>, and @ AVE OF OAKS protection inside decoded notes
  const corridorNote = decodeAppraiserNote(
    "4/1/26 FR: COMMERCIAL SIGN @ US 290 & @ AVE OF OAKS, KEPT CDU @ GD"
  );
  assert.equal(corridorNote.entries.length, 1);
  assert.equal(
    corridorNote.entries[0].text,
    "Commercial sign @ US 290 & @ ave of oaks, kept condition rating at Good."
  );

  // All 33 subareas (including WDU, CP5, CP6, MPL, FUL) + plural/MLS/garage shorthand
  const wduParen = decodeAppraiserNote("ADDED WDU(240) & CP5(80)");
  assert.equal(wduParen.hasShorthand, true);
  assert.equal(
    wduParen.entries[0].text,
    "Added elevated wood deck (240 sq ft) & canopy (80 sq ft)"
  );

  const wduVerb = decodeAppraiserNote("4/1/26 FR: ADDED WDU & REMOVED FUL");
  assert.equal(
    wduVerb.entries[0].text,
    "Added elevated wood deck & removed frame utility room."
  );

  const mlsPlural = decodeAppraiserNote("3 BDRMS, 2 BTHS & KIT UPDATED PER MLS");
  assert.equal(mlsPlural.hasShorthand, true);
  assert.equal(
    mlsPlural.entries[0].text,
    "3 bedrooms, 2 baths & kitchen updated per real-estate listing (MLS)"
  );

  const poSubmitted = decodeAppraiserNote("PO SUBMITTED PICS OF NEW DET GAR");
  assert.equal(poSubmitted.hasShorthand, true);
  assert.equal(
    poSubmitted.entries[0].text,
    "Property owner submitted photos of new detached garage."
  );

  // Pass-2 regression checks: singular ADDT/ADDTN vs plural ADDTNS, hyphenated ADJ BLDG-1 TO GD, bare <DATE> - <CODE>, and W/H & W/D
  const rearAddt = decodeAppraiserNote("REAR ADDT COMPLETED 2021");
  assert.equal(rearAddt.hasShorthand, true);
  assert.equal(rearAddt.entries[0].text, "Rear addition completed 2021.");

  const sideAddtn = decodeAppraiserNote("SIDE ADDTN & 2 ADDTNS @ TOI");
  assert.equal(sideAddtn.entries[0].text, "Side addition & 2 additions at time of inspection.");

  const adjBldg = decodeAppraiserNote("ADJ BLDG-1 TO GD");
  assert.equal(adjBldg.hasShorthand, true);
  assert.equal(adjBldg.entries[0].text, "Adjusted building-1 to Good.");

  const bareDateCode = decodeAppraiserNote("12/15 - FR");
  assert.equal(bareDateCode.hasShorthand, true);
  assert.deepEqual(bareDateCode.entries, [
    {
      date: "Dec 2015",
      kind: "Field review",
      text: "Appraisal check logged.",
    },
  ]);

  const applianceSlash = decodeAppraiserNote("4/1/26 FR: NEW W/H & W/D @ TOI");
  assert.equal(applianceSlash.entries.length, 1);
  assert.equal(
    applianceSlash.entries[0].text,
    "New water heater & washer/dryer at time of inspection."
  );

  // Given plain-English notes without shorthand (must not trigger <details> chrome)
  for (const plain of [
    "Homestead exemption verified by field staff.",
    "Owner states rear porch is not visible from us.",
    "Driveway access relocated to Ave H in 2019.",
    "Parcel fronts to US 59 feeder road.",
    "Parcel @ US 59 feeder road.",
    "Access easement @ PR 12.",
    "Corner entrance @ Ave of Oaks.",
  ]) {
    const res = decodeAppraiserNote(plain);
    assert.equal(res.hasShorthand, false, `Expected plain note for: ${plain}`);
    assert.deepEqual(res.entries, []);
    assert.equal(res.raw, plain);
  }

  assert.deepEqual(decodeAppraiserNote(""), { hasShorthand: false, entries: [], raw: "" });
  assert.deepEqual(decodeAppraiserNote(null), { hasShorthand: false, entries: [], raw: "" });
});

test("UT-M13-4: explainPermitRecord and explainExtraFeatureDesc decode municipal shorthand while returning empty string on plain descriptions", () => {
  assert.equal(
    explainPermitRecord({ d: "2019 OMITTED IMPS ADDED TO ROLL" }),
    "Previously unassessed building or addition added retroactively to the HCAD tax roll (not a new City permit)."
  );
  assert.equal(
    explainPermitRecord({ d: "ONE-STOP RE-ROOF RESIDENTIAL" }),
    "Fast-track City of Houston One-Stop roof replacement permit (same-day review for non-structural roof work)."
  );
  assert.equal(
    explainPermitRecord({ d: "ONE-STOP REMODEL, SINGLE FAMILY RESIDENTIAL" }),
    "Fast-track City of Houston residential remodel permit (streamlined review for non-structural interior or exterior updates)."
  );
  assert.equal(
    explainPermitRecord({ d: "ONE-STOP SIDING REPLACEMENT" }),
    "Fast-track City of Houston One-Stop permit (same-day review for minor, non-structural residential work)."
  );
  assert.equal(
    explainPermitRecord({ d: "RESIDENTIAL REPAIRS AS PER SPEC LIST" }),
    "Itemized residential repair scope filed on the City of Houston repair specification checklist (commonly foundation, framing, siding, or storm repairs)."
  );
  assert.equal(
    explainPermitRecord({ d: "DEMO RES/SEWER DISC" }),
    "Residential teardown and municipal sewer line disconnection permit (pre-demolition utility cap-off)."
  );
  assert.equal(
    explainPermitRecord({ d: "DEMO RES/SEWER DISC COMPLETE" }),
    "Residential teardown and municipal sewer line disconnection permit (pre-demolition utility cap-off)."
  );
  assert.equal(
    explainPermitRecord({ d: "DEMO COM/SEWER DISC" }),
    "Commercial building demolition and municipal sewer line cap-off permit."
  );
  assert.equal(
    explainPermitRecord({ d: "INTERIOR NON-LOAD BEARING PARTITIONS ONLY" }),
    "Interior non-structural partition remodel only (not a full building demolition)."
  );
  assert.equal(
    explainPermitRecord({ d: "HCHA SINGLE FAMILY REPAIR" }),
    "Houston Housing Authority / disaster housing rehabilitation program repair permit."
  );
  assert.equal(
    explainPermitRecord({ d: "COH - MINOR HOME REPAIR" }),
    "City of Houston housing rehabilitation program repair permit."
  );
  assert.equal(
    explainPermitRecord({ d: "CH 19 FLOODPLAIN ELEVATION" }),
    "City of Houston Chapter 19 floodplain repair, mitigation, or elevation compliance permit."
  );
  assert.equal(
    explainPermitRecord({ d: "SUBAREAS (581 SQ FT ADDED)" }),
    "HCAD appraisal sketch update recording added building subareas (porches, garages, or room additions)."
  );
  assert.equal(
    explainPermitRecord({ d: "EXTRA FEATURES (OB&Y)", tp: "Outbuilding / Yard Feature (OB&Y)" }),
    "HCAD outbuilding and yard feature record (pool, detached shed/garage, canopy, patio, or paving)."
  );
  assert.equal(
    explainPermitRecord({ d: "OB&Y - DETACHED SHED", tp: "Outbuilding / Yard Feature (OB&Y)" }),
    "HCAD outbuilding and yard feature record (pool, detached shed/garage, canopy, patio, or paving)."
  );
  assert.equal(
    explainPermitRecord({ d: "Detached Brick Shed", tp: "Outbuilding / Yard Feature (OB&Y)" }),
    ""
  );
  assert.equal(
    explainPermitRecord({ d: "ABATEMENT OF 420 SF RACM", src: "tx_dshs" }),
    "State-mandated Texas DSHS asbestos survey or abatement filing (required 10 working days before demolition or major renovation)."
  );
  assert.equal(
    explainPermitRecord({ d: "4D DANGEROUS BLDG ORDER" }),
    "City of Houston Neighborhood Protection dangerous-building enforcement / demolition order."
  );
  assert.equal(
    explainPermitRecord({ d: "LEASE SPACE BUILD-OUT STE 200" }),
    "Commercial interior tenant build-out or phased construction inside an existing building shell."
  );
  assert.equal(
    explainPermitRecord({ d: "2021 IRC COMPLIANCE REVIEW" }),
    "Reviewed under International Residential, Building, or Energy Conservation Code standards."
  );
  assert.equal(
    explainPermitRecord({ d: "SFD RMDL & ADDN W/ FND RPRS & DET GAR" }),
    "Includes single-family residence, remodel, building addition, repairs, foundation work, detached garage."
  );
  assert.equal(explainPermitRecord({ d: "Replace kitchen cabinets and paint" }), "");
  assert.equal(explainPermitRecord(null), "");

  // Extra-feature decoder
  assert.equal(
    explainExtraFeatureDesc("CRACKED/DAMAGED CONC SLAB"),
    "HCAD structural depreciation adjustment noting foundation or concrete slab cracking/settlement."
  );
  assert.equal(
    explainExtraFeatureDesc("RESIDENTIAL GUNITE SWIMMING POOL"),
    "In-ground shotcrete/concrete (gunite) residential swimming pool."
  );
  assert.equal(
    explainExtraFeatureDesc("CANOPY - ROOF EXTENSION"),
    "Attached patio cover or roofline extension."
  );
  assert.equal(
    explainExtraFeatureDesc("FRAME UTILITY SHED"),
    "Detached wood-frame storage or utility shed."
  );
  assert.equal(
    explainExtraFeatureDesc("RESIDENTIAL REINFORCED CONCRETE PAVING"),
    "Reinforced concrete driveway, flatwork, or site paving."
  );
  assert.equal(
    explainExtraFeatureDesc("TIMBER BULKHEAD"),
    "Waterfront or drainage shoreline retaining wall (bulkhead)."
  );
  assert.equal(
    explainExtraFeatureDesc("CUSTOM COMMERCIAL CANOPY STRUCTURAL/STL"),
    "40-character HCAD appraisal table entry (label truncated in county CAMA export)."
  );
  assert.equal(explainExtraFeatureDesc("Detached Brick Garage"), "");
  assert.equal(explainExtraFeatureDesc(""), "");
});

test("UT-M13-5: buildInspectorPermitsBodyHtml enforces XSS escaping, VALID_PERMIT_CAT_CODES sanitization, collapsible verbatim disclosure, and conditional sub-lines", () => {
  // 1. Adversarial XSS & invalid category class injection
  const xssHtml = buildInspectorPermitsBodyHtml({
    origYr: 1920,
    remodYr: 2018,
    effYr: 1985,
    qualGrade: `<img src=x onerror="alert(1)">`,
    relocNote: `<script>alert("reloc")</script>`,
    appraiserNote: `4/1/26 NC: <svg onload="alert('note')"> @ TOI`,
    origPermit: `#100" onclick="alert(2)`,
    origCost: `$5,000<script>`,
    builder: `O'Connor & <Sons>`,
    permitsList: [
      {
        id: `999"><script>alert(3)</script>`,
        dt: `2025-01-01<img src=x>`,
        cat: `demo" onclick="evil()`,
        tp: `Residential <b>Demolition</b>`,
        d: `ONE-STOP REMODEL <script>evil()</script>`,
        src: "coh_weekly",
      },
      {
        id: "1001",
        dt: "2024-05-01",
        cat: "remodel",
        tp: "Additions or Remodeling",
        d: "Replace interior trim and paint",
      },
    ],
    extraFeatures: [
      {
        y: `1995<script>`,
        d: `FRAME UTILITY SHED <img src=x onerror=1>`,
      },
      {
        y: 2005,
        d: "Detached Brick Garage",
      },
    ],
    atlasMaxYear: 2026,
  });

  assert.equal(xssHtml.includes("<script"), false);
  assert.equal(xssHtml.includes("<img"), false);
  assert.equal(xssHtml.includes("<svg"), false);
  assert.equal(xssHtml.includes('onclick="evil'), false);
  assert.equal(xssHtml.includes("<b>Demolition</b>"), false);
  assert.ok(xssHtml.includes("cat-other"), "Invalid cat attribute must fall back to cat-other");
  assert.equal(xssHtml.includes('cat-demo"'), false);
  assert.ok(xssHtml.includes("COH Live Permit"));
  assert.ok(xssHtml.includes('<details class="permit-verbatim-details">'));
  assert.equal(xssHtml.includes("<details open"), false);
  assert.ok(
    xssHtml.includes('<summary class="permit-verbatim-summary">Verbatim HCAD text</summary>')
  );

  // Shorthand permit #999 and extra feature #1 get .permit-plain-english-sub; plain ones do not
  const subMatches = xssHtml.match(/class="permit-plain-english-sub"/g) || [];
  assert.equal(subMatches.length, 2);

  // 2. Plain appraiser note without shorthand renders 1-line without <details>
  const plainNoteHtml = buildInspectorPermitsBodyHtml({
    origYr: 1935,
    appraiserNote: "Homestead exemption verified by field staff.",
    permitsList: [],
    extraFeatures: [],
  });
  assert.ok(plainNoteHtml.includes("HCAD Appraiser Permit / Renovation Note:"));
  assert.ok(plainNoteHtml.includes("Homestead exemption verified by field staff."));
  assert.equal(plainNoteHtml.includes("permit-verbatim-details"), false);
  assert.equal(plainNoteHtml.includes("permit-appraiser-decoded-list"), false);
  assert.equal(plainNoteHtml.includes("permit-empty-note"), false);

  // 3. Empty state renders .permit-empty-note when no notes, permits, or extra features exist
  const emptyHtml = buildInspectorPermitsBodyHtml({
    origYr: 1925,
    permitsList: [],
    extraFeatures: [],
    atlasMaxYear: 2026,
  });
  assert.ok(emptyHtml.includes("No itemized 2004-2026 municipal building permits"));
});

test("UT-M13-6: Live permits_062.json.gz 1016 Algregg St (0620950000004), CSS/JS wiring, and zero U+2010/U+2013/U+2014 dashes uphold Milestone 13 invariants", () => {
  const shardPath = path.join(DATA_DIR, "permits", "permits_062.json.gz");
  const shard = JSON.parse(zlib.gunzipSync(fs.readFileSync(shardPath)).toString("utf8"));
  const algregg = shard["0620950000004"];
  assert.ok(algregg, "Expected 0620950000004 (1016 Algregg St) in permits_062.json.gz");
  assert.equal(algregg.yr, 1920);
  assert.equal(Array.isArray(algregg.p), true);
  assert.ok(
    algregg.p.length >= 2,
    `Expected >= 2 permits on 0620950000004, got ${algregg.p.length}`
  );

  const p2037787 = algregg.p.find((row) => row.id === "2037787");
  const p1138473 = algregg.p.find((row) => row.id === "1138473");
  assert.ok(p2037787, "Expected permit #2037787 on 0620950000004");
  assert.equal(p2037787.tp, "Additions or Remodeling");
  assert.equal(p2037787.cat, "remodel");
  assert.ok(VALID_PERMIT_CAT_CODES.has(p2037787.cat));

  assert.ok(p1138473, "Expected permit #1138473 on 0620950000004");
  assert.equal(p1138473.tp, "Repairs (Foundation / General Maintenance)");
  assert.equal(p1138473.cat, "repair");
  assert.ok(VALID_PERMIT_CAT_CODES.has(p1138473.cat));

  const rendered = buildInspectorPermitsBodyHtml({
    origYr: algregg.yr,
    qualGrade: algregg.qual,
    appraiserNote: algregg.note,
    permitsList: algregg.p,
    extraFeatures: algregg.xf || [],
    atlasMaxYear: 2026,
  });
  assert.ok(rendered.includes("Apr 1, 2026 (Permit check):"));
  assert.ok(rendered.includes("Nov 22, 2024 (Field review):"));
  assert.ok(rendered.includes("Additions or Remodeling"));
  assert.ok(rendered.includes("Repairs (Foundation / General Maintenance)"));
  assert.ok(rendered.includes("cat-remodel"));
  assert.ok(rendered.includes("cat-repair"));
  assert.equal(hasForbiddenDash(rendered), false);

  const manifest = JSON.parse(
    fs.readFileSync(path.join(DATA_DIR, "pmtiles_manifest.json"), "utf8")
  );
  const activeVer = String(manifest.cache_bust || "").trim();
  const appJsSrc = fs.readFileSync(path.join(REPO_ROOT, "app", "js", "app.js"), "utf8");
  assert.ok(
    appJsSrc.includes(`from "./permitPlainEnglish.js?v=${activeVer}"`),
    `app.js must import permitPlainEnglish.js?v=${activeVer}`
  );

  const cssSrc = fs.readFileSync(path.join(REPO_ROOT, "app", "styles", "index.css"), "utf8");
  for (const selector of [
    ".permit-verbatim-details",
    ".permit-verbatim-summary",
    ".permit-verbatim-raw",
    ".permit-cat-badge.cat-repair",
    ".permit-plain-english-sub",
  ]) {
    assert.ok(cssSrc.includes(selector), `index.css missing required selector ${selector}`);
  }
  for (const catCode of VALID_PERMIT_CAT_CODES) {
    if (catCode === "other") continue;
    assert.ok(
      cssSrc.includes(`.permit-cat-badge.cat-${catCode}`),
      `index.css missing .permit-cat-badge.cat-${catCode} for VALID_PERMIT_CAT_CODES`
    );
  }

  for (const relPath of [
    "app/js/permitPlainEnglish.js",
    "app/js/filterStore.js",
    "app/styles/index.css",
    "pipeline/atlas_pipeline/schema.py",
    "pipeline/atlas_pipeline/build_permits_and_contractors.py",
    "pipeline/atlas_pipeline/cli.py",
    "pipeline/tests/test_milestone13_permit_categories.py",
    "tests/unit/milestone13_permit_plain_english.test.mjs",
    "tests/e2e/test_milestone13_permit_plain_english.py",
  ]) {
    const content = fs.readFileSync(path.join(REPO_ROOT, relPath), "utf8");
    assert.equal(
      hasForbiddenDash(content),
      false,
      `${relPath} must contain zero U+2010, U+2013, or U+2014 dashes`
    );
  }
});

test("UT-M13-7: extractUrlInspectorTab and parseHashToState parse &tab= deep links across hash, search, viewport hashes, and back-navigation resets", () => {
  assert.deepEqual(VALID_INSPECTOR_TABS, ["overview", "history", "permits", "hcad"]);

  assert.equal(extractUrlInspectorTab("#hcad=0620950000004&tab=permits", ""), "permits");
  assert.equal(extractUrlInspectorTab("", "?tab=PERMITS"), "permits");
  assert.equal(extractUrlInspectorTab("#17/29.79/-95.38&tab=history", ""), "history");
  assert.equal(
    extractUrlInspectorTab("#hcad=0620950000004&tab=hcad", "?tab=overview"),
    "hcad"
  );
  assert.equal(extractUrlInspectorTab("#hcad=0620950000004", ""), "");
  assert.equal(extractUrlInspectorTab("#hcad=0620950000004&tab=evil", "?tab=script"), "");
  assert.equal(extractUrlInspectorTab("", ""), "");

  const deepLinked = parseHashToState("#hcad=0620950000004&tab=permits", "");
  assert.equal(deepLinked.selection?.hcadNum, "0620950000004");
  assert.equal(deepLinked.inspectorTab, "permits");

  const backReset = parseHashToState("#hcad=0620950000004", "");
  assert.equal(backReset.selection?.hcadNum, "0620950000004");
  assert.equal(backReset.inspectorTab, "");

  const queryTab = parseHashToState("#hcad=0620950000004", "?tab=HISTORY");
  assert.equal(queryTab.inspectorTab, "history");

  // Bidirectional round-trip: serializeStateToHash & buildShareableUrl preserve non-default inspectorTab on parcel/feature selections
  const serializedPermits = serializeStateToHash(
    DEFAULT_FILTER_STATE,
    { lat: 29.79, lng: -95.38, zoom: 17, pitch: 0 },
    { includeViewport: true, selectedHcad: "0620950000004", inspectorTab: "permits" }
  );
  assert.ok(serializedPermits.includes("hcad=0620950000004"));
  assert.ok(serializedPermits.includes("tab=permits"));
  const roundTrip = parseHashToState(`#${serializedPermits}`, "");
  assert.equal(roundTrip.selection?.hcadNum, "0620950000004");
  assert.equal(roundTrip.inspectorTab, "permits");

  // Default overview tab and boundary-only selections omit redundant tab= parameter
  const serializedOverview = serializeStateToHash(
    DEFAULT_FILTER_STATE,
    null,
    { selectedHcad: "0620950000004", inspectorTab: "overview" }
  );
  assert.equal(serializedOverview.includes("tab="), false);

  const serializedBoundary = serializeStateToHash(
    DEFAULT_FILTER_STATE,
    null,
    {
      selectedBoundary: { layerKey: "historic_districts", id: "Norhill Historic District" },
      inspectorTab: "permits",
    }
  );
  assert.equal(serializedBoundary.includes("tab="), false);

  const shareUrlPermits = buildShareableUrl(
    DEFAULT_FILTER_STATE,
    { lat: 29.79, lng: -95.38, zoom: 17, pitch: 0 },
    {
      baseOriginPath: "https://davemorris-gcp.github.io/houston-building-atlas/",
      selectedHcad: "0620950000004",
      inspectorTab: "permits",
      useQueryString: true,
    }
  );
  assert.ok(shareUrlPermits.includes("hcad=0620950000004"));
  assert.ok(shareUrlPermits.includes("tab=permits"));
});

test("UT-M14-1: explainCohWatchPermit decodes all COH early-warning permit types and live active_permit_alerts.json COH records", () => {
  // Stage 1 pre-demolition plumbing permit Type 12 / 4P
  const stage1Type12 = explainCohWatchPermit({
    permit_type: "12",
    use_desc: "BLD DEMO / SEWER DISC (1 OF 2) (202604512)",
    is_sewer_disconnect: true,
  });
  assert.ok(stage1Type12.includes("Stage 1 pre-demolition plumbing permit (Type 12/4P)"));
  assert.ok(stage1Type12.includes("Stage 2 Building Demolition (SD) permit is still required"));
  assert.equal(hasForbiddenDash(stage1Type12), false);

  const stage1Type4P = explainCohWatchPermit({
    permit_type: "4P",
    use_desc: "RES DEMO / SEWER DISC",
    is_sewer_disconnect: true,
  });
  assert.ok(stage1Type4P.includes("Stage 1 pre-demolition plumbing permit (Type 12/4P)"));

  // Stage 2 structural demolition permit Type SD
  const stage2Sd = explainCohWatchPermit({
    permit_type: "SD",
    use_desc: "BLD DEMO / SEWER DISC (2 OF 2)",
    is_structural_demo: true,
  });
  assert.ok(stage2Sd.includes("Stage 2 structural demolition permit (Type SD)"));
  assert.equal(hasForbiddenDash(stage2Sd), false);

  // Type 4D Dangerous Building / DAP List
  const type4D = explainCohWatchPermit({
    permit_type: "4D",
    use_desc: "COH D.B. / RESIDENTIAL DEMO - DAP LIST",
  });
  assert.ok(
    type4D.includes(
      "City of Houston Dangerous Building (Code Enforcement / Demolition Assistance Program Type 4D)"
    )
  );

  // Accessory teardowns: GARAGE ONLY, GARAGE APT ONLY, (NO SD REQUIRED)- GA
  const garageOnly = explainCohWatchPermit({
    permit_type: "SD",
    use_desc: "RES DEMO / SEWER DISC - GARAGE ONLY",
  });
  assert.ok(
    garageOnly.includes("Demolition permit limited to a detached garage, carport, or accessory structure")
  );
  assert.ok(garageOnly.includes("main building on the parcel is not covered"));

  const garageAptOnly = explainCohWatchPermit({
    permit_type: "SD",
    use_desc: "RES DEMO / SEWER DISC - GARAGE APT ONLY",
  });
  assert.ok(
    garageAptOnly.includes("Demolition permit limited to a detached garage apartment on the parcel")
  );

  const noSdGa = explainCohWatchPermit({
    permit_type: "12",
    use_desc: "RES DEMO / SEWER DISC (NO SD REQUIRED)- GA",
  });
  assert.ok(noSdGa.includes("Detached garage or accessory structure teardown permit"));
  assert.ok(noSdGa.includes("no separate sewer disconnection permit required"));

  // PPR DEMO OF NON-LOAD BEARING/NON- FIRE R Type 13 and Type PX plan-review fees
  const pprType13 = explainCohWatchPermit({
    permit_type: "13",
    use_desc: "PPR DEMO OF NON-LOAD BEARING/NON- FIRE R",
  });
  assert.ok(pprType13.includes("Private Provider Review (PPR) commercial building permit (Type 13)"));
  assert.ok(pprType13.includes("interior non-load-bearing, non-fire-rated partition removal only"));

  const pprTypePx = explainCohWatchPermit({
    permit_type: "PX",
    use_desc: "PPR DEMO OF NON-LOAD BEARING/NON- FIRE R",
  });
  assert.ok(pprTypePx.includes("City of Houston commercial plan-review fee (Type PX)"));
  assert.ok(pprTypePx.includes("interior non-load-bearing, non-fire-rated partition removal only"));

  assert.equal(explainCohWatchPermit(null), "");
  assert.equal(explainCohWatchPermit({}), "");

  // Verify every live coh_permits entry in active_permit_alerts.json decodes cleanly with zero forbidden dashes
  const alertsPayload = JSON.parse(
    fs.readFileSync(path.join(DATA_DIR, "active_permit_alerts.json"), "utf8")
  );
  let cohCount = 0;
  for (const prop of Object.values(alertsPayload.alerts || {})) {
    for (const cp of prop.coh_permits || []) {
      cohCount++;
      const note = explainCohWatchPermit(cp);
      assert.ok(note.length > 20, `Expected non-empty COH explanation for ${JSON.stringify(cp)}`);
      assert.equal(hasForbiddenDash(note), false);
    }
  }
  assert.ok(cohCount >= 10, `Expected at least 10 live COH watch permits, got ${cohCount}`);
});

test("UT-M14-2: explainDshsNotification decodes all TX DSHS notification categories and live active_permit_alerts.json DSHS records", () => {
  // Demolition 10-working-day NESHAP notice
  const demoNotice = explainDshsNotification({
    work_type: "Demolition",
    work_description: "Notification Type: Demolition; Procedures: Wet Method",
  });
  assert.ok(
    demoNotice.includes(
      "State-mandated 10-working-day advance notice of structural demolition filed with Texas DSHS under federal NESHAP"
    )
  );
  assert.equal(hasForbiddenDash(demoNotice), false);

  // O & M Work localized maintenance
  const omWork = explainDshsNotification({
    work_type: "Abatement",
    work_description:
      "Notification Type: Abatement, O & M Work; Procedures: Glove Bag; RACM: 12 Pipes (Linear Feet)",
  });
  assert.ok(
    omWork.includes("Routine Operations and Maintenance (O&M) asbestos management filing")
  );

  // Full Containment (296.212) / negative-pressure RACM abatement
  const fullContainment = explainDshsNotification({
    work_type: "Abatement",
    work_description:
      "Notification Type: Abatement; Procedures: Full Containment (296.212), Negative Pressure, Wet Method; RACM: 4,250 Surface Area (Square Feet)",
  });
  assert.ok(
    fullContainment.includes(
      "State-mandated 10-working-day advance asbestos abatement filing under sealed negative-pressure containment (25 TAC Sec. 296.212)"
    )
  );

  // Category I/II NonFriable floor tile & mastic removal
  const nonFriable = explainDshsNotification({
    work_type: "Abatement",
    work_description:
      "Notification Type: Abatement; Procedures: Wet Method; Category I NonFriable: 1,800 Surface Area (Square Feet); Category II NonFriable: 200 Surface Area (Square Feet)",
  });
  assert.ok(
    nonFriable.includes(
      "State-mandated 10-working-day advance notice to remove non-friable asbestos-containing floor tile, adhesive mastic, or caulking"
    )
  );

  // General RACM abatement
  const generalRacm = explainDshsNotification({
    work_type: "Abatement",
    work_description: "RACM: 600 Surface Area (Square Feet)",
  });
  assert.ok(
    generalRacm.includes(
      "State-mandated 10-working-day advance notice to remove Regulated Asbestos-Containing Material (RACM)"
    )
  );

  assert.equal(explainDshsNotification(null), "");
  assert.equal(explainDshsNotification({}), "");

  // Verify every live dshs_notifications entry in active_permit_alerts.json decodes cleanly
  const alertsPayload = JSON.parse(
    fs.readFileSync(path.join(DATA_DIR, "active_permit_alerts.json"), "utf8")
  );
  let dshsCount = 0;
  for (const prop of Object.values(alertsPayload.alerts || {})) {
    for (const dn of prop.dshs_notifications || []) {
      dshsCount++;
      const note = explainDshsNotification(dn);
      assert.ok(note.length > 20, `Expected non-empty DSHS explanation for ${JSON.stringify(dn)}`);
      assert.equal(hasForbiddenDash(note), false);
    }
  }
  assert.ok(dshsCount >= 10, `Expected at least 10 live DSHS notifications, got ${dshsCount}`);
});

test("UT-M14-3: explainMultiAgencyFiling decodes TDLR TABS, Houston 311 Dangerous Buildings, DPV, C3N, C2R, C3F, and TABC", () => {
  const tdlr = explainMultiAgencyFiling({
    agency: "TDLR TABS",
    title: "TDLR Project #TABS2026009812: Interior Renovation ($350,000)",
  });
  assert.ok(tdlr.includes("Texas Department of Licensing and Regulation (TDLR) Architectural Barriers"));
  assert.ok(tdlr.includes("$50,000"));

  const don311 = explainMultiAgencyFiling({
    agency: "Houston 311 Dangerous Buildings",
    title: "Houston 311 Case #101005118923: Dangerous Buildings Investigation",
  });
  assert.ok(don311.includes("Department of Neighborhoods (DON) Dangerous Building investigation"));

  const dpvPlain = explainMultiAgencyFiling({
    agency: "Planning Commission",
    title: "DPV - Development Plat Variance for reduced front building line",
  });
  assert.ok(dpvPlain.includes("Chapter 42 Development Plat Variance (Section K DPV)"));

  const dpvCmu = explainMultiAgencyFiling({
    agency: "Planning Commission",
    title: "DPV - Development Plat Variance for 6-ft CMU wall",
  });
  assert.ok(dpvCmu.includes("CMU = concrete masonry unit"));

  const c3n = explainMultiAgencyFiling({
    agency: "Planning Commission",
    title: "C3N - Class 3 Subdivision Replat with public hearing notice",
  });
  assert.ok(c3n.includes("Chapter 42 Class 3 Subdivision Replat (C3N)"));

  const c2r = explainMultiAgencyFiling({
    agency: "Planning Commission",
    title: "C2R - Class 2 Subdivision Replat",
  });
  assert.ok(c2r.includes("Chapter 42 Class 2 Subdivision Replat (C2R)"));

  const c3f = explainMultiAgencyFiling({
    agency: "Planning Commission",
    title: "C3F - Class 3 Final Subdivision Plat",
  });
  assert.ok(c3f.includes("Chapter 42 Class 3 Final Subdivision Plat (C3F)"));

  const tabc = explainMultiAgencyFiling({
    agency: "TABC",
    title: "Mixed Beverage Permit Application (MB)",
  });
  assert.ok(tabc.includes("Texas Alcoholic Beverage Commission (TABC)"));

  assert.equal(explainMultiAgencyFiling(null), "");
  assert.equal(explainMultiAgencyFiling({}), "");

  const alertsPayload = JSON.parse(
    fs.readFileSync(path.join(DATA_DIR, "active_permit_alerts.json"), "utf8")
  );
  for (const prop of Object.values(alertsPayload.alerts || {})) {
    for (const mf of prop.multi_agency_filings || []) {
      const note = explainMultiAgencyFiling(mf);
      assert.ok(note.length > 20, `Expected non-empty multi-agency explanation for ${JSON.stringify(mf)}`);
      assert.equal(hasForbiddenDash(note), false);
    }
  }
});

test("UT-M14-4: explainWatchlistCheckText decodes chk-05, chk-09, chk-10, chk-13, chk-14, chk-15 and returns empty string on quiet/routine/degraded lines", () => {
  // chk-05: HCAD Impr $0
  const chk05 = explainWatchlistCheckText(
    "[AUTO-SWEEP] HCAD 0610990150001: Owner COUNTY OF HARRIS (Class X3, Impr $0, Land $985,163)",
    "chk-05"
  );
  assert.ok(chk05.includes("HCAD appraisal roll lists $0 improvement value"));

  // chk-09: Chapter 42 DPV / C3N / C2R / C3F
  const chk09Dpv = explainWatchlistCheckText(
    "Agenda #92 (App 2026-0412): Development Plat Variance (DPV) for 6-ft CMU wall",
    "chk-09"
  );
  assert.ok(chk09Dpv.includes("Chapter 42 Development Plat Variance (Section K DPV)"));
  assert.ok(chk09Dpv.includes("CMU = concrete masonry unit"));

  const chk09C3n = explainWatchlistCheckText(
    "Agenda #14 (App 2026-0510): Class 3 Subdivision Replat (C3N)",
    "chk-09"
  );
  assert.ok(chk09C3n.includes("Chapter 42 Class 3 Subdivision Replat (C3N)"));

  // chk-10: TDLR TABS
  const chk10 = explainWatchlistCheckText(
    "[AUTO-SWEEP] TDLR Project #TABS2026011204 registered ($850,000)",
    "chk-10"
  );
  assert.ok(chk10.includes("Texas Department of Licensing and Regulation (TDLR) Architectural Barriers"));

  // chk-13: Houston 311 Dangerous Buildings
  const chk13 = explainWatchlistCheckText(
    "[AUTO-SWEEP] Houston 311 Case #2600399812: Dangerous Buildings Investigation (Open)",
    "chk-13"
  );
  assert.ok(chk13.includes("City of Houston Department of Neighborhoods (DON) Dangerous Building investigation"));

  // chk-14: COH Demo / Sewer Disconnect / Garage Only / PPR Demo
  const chk14Stage1 = explainWatchlistCheckText(
    "[AUTO-SWEEP] COH Permit #2026019881: BLD DEMO / SEWER DISC (1 OF 2)",
    "chk-14"
  );
  assert.ok(chk14Stage1.includes("Stage 1 pre-demolition plumbing permit (Type 12/4P)"));

  const chk14Garage = explainWatchlistCheckText(
    "[AUTO-SWEEP] COH Permit #2026019999: RES DEMO / SEWER DISC - GARAGE ONLY",
    "chk-14"
  );
  assert.ok(
    chk14Garage.includes("Demolition permit limited to a detached garage, carport, or accessory structure")
  );

  // chk-15: TX DSHS Asbestos & Demolition
  const chk15 = explainWatchlistCheckText(
    "[AUTO-SWEEP] DSHS Notification #12345678: Notification Type: Abatement; Procedures: Full Containment (296.212); RACM: 1,500 Surface Area (Square Feet)",
    "chk-15"
  );
  assert.ok(
    chk15.includes(
      "State-mandated 10-working-day advance asbestos abatement filing under sealed negative-pressure containment (25 TAC Sec. 296.212)"
    )
  );

  // Quiet / routine strings MUST return "" even when checkIdOrKind is chk-10, chk-13, or chk-15
  const quietInputs = [
    ["", ""],
    ["no results", "chk-14"],
    ["no new results", "chk-10"],
    ["no records found", "chk-13"],
    ["no demo permit", "chk-14"],
    ["No new filings", "chk-15"],
    ["No new filings.", "chk-13"],
    ["Checked Oct 26, 2026 by Preservation Staff - No new filings found.", "chk-13"],
    ["No new filings found (checked DSHS portal).", "chk-15"],
    ["[AUTO-SWEEP] 0 active DSHS notifications found for parcel.", "chk-15"],
    ["[AUTO-SWEEP] No matching TDLR TABS projects found.", "chk-10"],
    ["[AUTO-SWEEP] No open 311 Dangerous Building cases found.", "chk-13"],
    ["Checked Nov 09, 2026 - no demo permits found.", "chk-14"],
    ["old/irrelevant results", "chk-10"],
    [
      "2 agency endpoint(s) did not complete cleanly: TDLR Architectural Barriers (TABS) (HTTP 503), TABC (timeout)",
      "",
    ],
    [
      "Daily 8-Agency Sweep Degraded (6/8 agencies OK) 2 agency endpoint(s) did not complete cleanly: TDLR Architectural Barriers (TABS) (HTTP 503)",
      "watchlist_sweep_degraded",
    ],
    [
      "Daily 8-Agency Sweep Degraded (6/8 agencies OK) 2 agency endpoint(s) did not complete cleanly: TDLR Architectural Barriers (TABS) (HTTP 503)",
      "",
    ],
  ];
  for (const [text, kind] of quietInputs) {
    assert.equal(
      explainWatchlistCheckText(text, kind),
      "",
      `Expected quiet/degraded string "${text}" (${kind}) to return ""`
    );
  }
});

test("UT-M14-5: Activity log helpers, DOM wiring in app.js & plat-monitor.html, cache-bust 20261109c, and zero forbidden dashes", () => {
  // 1. sanitizeActivityEventForRender
  const cohEvent = {
    id: "ev-coh-test",
    stream: "permits",
    kind: "permit_coh_alert",
    severity: "critical",
    adverse: true,
    localDate: "2026-11-08",
    title: "COH Structural Demo / Sewer Permit: 2000 Smith St",
    summary: "BLD DEMO / SEWER DISC (1 OF 2) <script>alert(1)</script>",
    hcad: "0152300000001",
    permitMeta: {
      alertKind: "coh",
      record: {
        permit_type: "12",
        use_desc: "BLD DEMO / SEWER DISC (1 OF 2)",
        is_sewer_disconnect: true,
      },
    },
  };
  const sanitizedCoh = sanitizeActivityEventForRender(cohEvent);
  assert.ok(
    sanitizedCoh.plainEnglishNote.includes("Stage 1 pre-demolition plumbing permit (Type 12/4P)")
  );
  assert.equal(sanitizedCoh.safePlainEnglishNote.includes("<script>"), false);

  // Degraded sweep event must NOT populate plainEnglishNote or safePlainEnglishNote
  const degradedEvent = {
    id: "ev-degraded-test",
    stream: "watchlist",
    kind: "watchlist_sweep_degraded",
    severity: "warning",
    adverse: false,
    localDate: "2026-11-08",
    title: "Daily 8-Agency Sweep Degraded (6/8 agencies OK)",
    summary:
      "2 agency endpoint(s) did not complete cleanly: TDLR Architectural Barriers (TABS) (HTTP 503), TABC (timeout).",
  };
  const sanitizedDegraded = sanitizeActivityEventForRender(degradedEvent);
  assert.equal(sanitizedDegraded.plainEnglishNote, "");
  assert.equal(sanitizedDegraded.safePlainEnglishNote, "");

  // 2. Verify live monitor_activity_log.json events sanitize cleanly with zero forbidden dashes
  const activityLog = JSON.parse(
    fs.readFileSync(path.join(DATA_DIR, "monitor_activity_log.json"), "utf8")
  );
  let plainEventCount = 0;
  for (const ev of activityLog.events || []) {
    const s = sanitizeActivityEventForRender(ev);
    if (ev.kind === "watchlist_sweep_degraded") {
      assert.equal(s.plainEnglishNote, "", `Degraded event ${ev.id} must not have plainEnglishNote`);
    }
    if (s.plainEnglishNote) {
      plainEventCount++;
      assert.equal(hasForbiddenDash(s.plainEnglishNote), false);
    }
  }
  assert.ok(
    plainEventCount >= 30,
    `Expected at least 30 activity log events with Plain-English notes, got ${plainEventCount}`
  );

  // 3. Verify app.js, plat-monitor.html, and index.css DOM classes and cache-bust 20261109c
  assert.equal(ACTIVE_VER, "20261109c");
  const appJs = fs.readFileSync(path.join(REPO_ROOT, "app", "js", "app.js"), "utf8");
  const platMonitorHtml = fs.readFileSync(
    path.join(REPO_ROOT, "app", "plat-monitor.html"),
    "utf8"
  );
  const indexCss = fs.readFileSync(path.join(REPO_ROOT, "app", "styles", "index.css"), "utf8");

  assert.ok(appJs.includes("inspector-permit-filing-plain"));
  assert.ok(appJs.includes("pw-item-filing-plain"));
  assert.ok(platMonitorHtml.includes("wl-check-plain-note"));
  assert.ok(indexCss.includes(".inspector-permit-filing-plain"));
  assert.ok(indexCss.includes(".pw-item-filing-plain"));
  assert.ok(platMonitorHtml.includes(".act-card-plain-note"));
  assert.ok(platMonitorHtml.includes(".wl-check-plain-note"));

  // 4. Verify zero U+2010/U+2013/U+2014 dashes across all modified files
  const checkedFiles = [
    path.join(REPO_ROOT, "app", "js", "permitPlainEnglish.js"),
    path.join(REPO_ROOT, "app", "js", "activityLogHelpers.js"),
    path.join(REPO_ROOT, "app", "js", "app.js"),
    path.join(REPO_ROOT, "app", "plat-monitor.html"),
    path.join(REPO_ROOT, "app", "styles", "index.css"),
  ];
  for (const fp of checkedFiles) {
    const content = fs.readFileSync(fp, "utf8");
    assert.equal(
      hasForbiddenDash(content),
      false,
      `Forbidden unicode dash (U+2010/U+2013/U+2014) found in ${path.relative(REPO_ROOT, fp)}`
    );
  }
});

